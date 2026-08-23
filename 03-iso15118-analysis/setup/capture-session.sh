#!/usr/bin/env bash
# Capture one full ISO 15118 session as a pcap — without sudo.
# 抓取一次完整的 ISO 15118 会话为 pcap —— 不需要 sudo。
#
# Each container has its own network namespace, so tcpdump on the host sees
# nothing. Rather than nsenter (which needs root), this runs a throwaway
# container with --net=container:iso15118-secc-1, so it shares the station's
# namespace and its eth0 — SDP multicast included. Docker group membership is
# all that is required.
# 容器各自在独立的网络命名空间里，宿主机上的 tcpdump 什么也抓不到。
# 这里不用 nsenter(需要 root)，而是起一个临时容器共享 SECC 的命名空间，
# 抓它的 eth0，包括 SDP 组播。只要在 docker 组里就行。

set -euo pipefail

STACK="${STACK:-$HOME/codespace/iso15118}"
OUTDIR="${OUTDIR:-$HOME/codespace/ev-charging-lab/03-iso15118-analysis/captures}"
OUTFILE="${1:-session-02.pcap}"
CAPNAME="v2gcap-run"

[ -d "$STACK" ] || { echo "stack not found at $STACK"; exit 1; }

# The capture image is tiny (alpine + tcpdump). Build it once.
# 抓包镜像很小(alpine + tcpdump)，只建一次。
if ! docker image inspect v2gcap >/dev/null 2>&1; then
  echo "==> building the capture image (once)"
  printf 'FROM alpine:latest\nRUN apk add --no-cache tcpdump\n' | docker build -q -t v2gcap - >/dev/null
fi

docker rm -f "$CAPNAME" >/dev/null 2>&1 || true

echo "==> starting SECC + redis (EVCC stays down so the capture can be armed first)"
cd "$STACK"
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d secc redis >/dev/null

# Wait on the listener being ready, not on a fixed number of seconds.
# NOTE: `docker logs ... | grep -q` looks nicer but is broken under
# `set -o pipefail`: grep -q exits on the first match, docker logs then dies of
# SIGPIPE (141), and pipefail reports the whole pipeline as failed — so a
# successful match reads as a miss and the loop runs its full timeout.
# 注意: `docker logs ... | grep -q` 看着更漂亮，但在 `set -o pipefail` 下是坏的:
# grep -q 一匹配就退出，docker logs 随即因 SIGPIPE(141) 死掉，pipefail 于是把
# 整条管道判为失败 —— 匹配成功被读成失败，循环白等到超时。
# 等监听真正就绪，而不是死等固定秒数。
echo -n "==> waiting for the SDP listener "
for _ in $(seq 60); do
  case "$(docker logs iso15118-secc-1 2>&1)" in *"UDP server started"*) echo "ok"; break;; esac
  echo -n "."; sleep 0.5
done

echo "==> arming tcpdump inside the SECC's network namespace"
docker run -d --name "$CAPNAME" \
  --net="container:iso15118-secc-1" \
  -v "$OUTDIR:/out" v2gcap \
  tcpdump -i eth0 -w "/out/$OUTFILE" -U >/dev/null
sleep 2                                    # let the capture socket open

echo "==> starting EVCC — this triggers the session"
docker start iso15118-evcc-1 >/dev/null
docker wait iso15118-evcc-1 >/dev/null     # blocks until the EV side finishes

# The spec closes the TCP connection 5 s after SessionStopRes. Wait for the
# station to say so rather than guessing.
# 规范规定 SessionStopRes 之后 5 秒才关 TCP。等桩自己说关了，不要猜。
echo -n "==> waiting for the TCP teardown "
for _ in $(seq 40); do
  case "$(docker logs iso15118-secc-1 2>&1)" in *"TCP connection closed"*) echo "ok"; break;; esac
  echo -n "."; sleep 0.5
done

echo "==> stopping capture"
docker stop "$CAPNAME" >/dev/null          # SIGTERM makes tcpdump flush and exit
docker rm "$CAPNAME" >/dev/null
docker run --rm -v "$OUTDIR:/out" alpine \
  chown "$(id -u):$(id -g)" "/out/$OUTFILE" >/dev/null

echo
ls -lh "$OUTDIR/$OUTFILE"
echo
echo "Open it:  wireshark $OUTDIR/$OUTFILE"
echo "Filters / 过滤器:"
echo "  udp.port == 15118   SDP discovery — two packets, the whole bootstrap"
echo "  v2gtp               the 8-byte header, dissected by Wireshark"
echo "  tcp                 the EXI-carrying session"
