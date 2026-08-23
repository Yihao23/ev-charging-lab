# Running an ISO 15118 SECC and EVCC locally
# 在本地跑起 ISO 15118 的 SECC 和 EVCC

No hardware needed. Both ends are software; they talk over a local network
interface instead of a PLC modem.
不需要硬件。两端都是软件，通过本地网络接口通信，而不是 PLC 调制解调器。

---

## Option A — EcoG-io/iso15118 (Python, recommended to start)
## 方案 A —— EcoG-io/iso15118 (Python，推荐入门)

**This is the one that produced [`session-01`](../captures/).** The repo ships a
Docker path that is simpler than the `veth` setup below — use it first.
**`session-01` 就是这么跑出来的。** 仓库自带的 Docker 路径比下面的 `veth` 方案简单，先用它。

```bash
cd ~/codespace                     # a sibling of this repo, NOT inside it
git clone https://github.com/EcoG-io/iso15118.git
cd iso15118
make build                         # generates the PKI, then builds both images
make dev                           # brings up secc + evcc + redis
```

Clone it **outside** `ev-charging-lab` — it is its own git repo, and `.gitignore`
here has no rule for it. Only the logs it produces belong in this repo.
克隆到 `ev-charging-lab` **外面** —— 它本身是个 git 仓库，这里的 `.gitignore` 也没有
针对它的规则。只有它产出的日志才属于本仓库。

### Two fixes needed as of 2026-08 / 2026-08 时需要的两处修补

**1. `docker-compose: command not found`** — the `Makefile` calls the retired
standalone binary; modern Docker ships compose as a subcommand. A shim on
`PATH` fixes it for every old project, not just this one:
`Makefile` 调的是已淘汰的独立二进制，新版 Docker 把 compose 做成了子命令。
在 `PATH` 上放个转发脚本，一次解决所有老项目:

```bash
printf '#!/bin/sh\nexec docker compose "$@"\n' > ~/.local/bin/docker-compose
chmod +x ~/.local/bin/docker-compose
```

**2. `apt update` → `404 Not Found` on `deb.debian.org/debian buster`** — the
base image is `python:3.10.0-buster`, and Debian buster is EOL: its repos moved
to `archive.debian.org`, so `apt install default-jre` fails with exit code 100.
Bump the base image in **`template.Dockerfile`** (both stages) — the `Makefile`
regenerates `iso15118/{secc,evcc}/Dockerfile` from it on every build, so editing
the generated files is pointless:
基础镜像是 `python:3.10.0-buster`，而 buster 已 EOL，源被移到了 `archive.debian.org`，
所以 `apt install default-jre` 以 exit code 100 失败。改 **`template.Dockerfile`**
的两处 `FROM` —— `Makefile` 每次构建都从它重新生成那两个 Dockerfile，改生成物没用:

```bash
sed -i 's/python:3\.10\.0-buster/python:3.10-bullseye/g' template.Dockerfile
```

> Why a JRE is needed at all: the EXI codec is `iso15118/shared/EXICodec.jar`
> (EXIficient), driven from Python over py4j. Real charge points use a C codec
> — OpenV2G or libcbv2g — because embedded hardware has no JVM.
> 为什么需要 JRE: EXI 编解码器是 `iso15118/shared/EXICodec.jar`(EXIficient)，
> Python 通过 py4j 调它。真桩用的是 C 实现(OpenV2G / libcbv2g)，嵌入式硬件没有 JVM。

### What a good run looks like / 跑成功是什么样

```
UDP server started at address FF02::1%eth0 and port 15118    ← SDP listener
SDPRequest received: [Security: NO_TLS, Protocol: TCP]
TCP server started at address fe80::…%eth0 and port 50933    ← ephemeral, announced via SDP
Entered state SupportedAppProtocol
…
Sent SessionStopRes
Session ended in SessionStop (EV requested to terminate the communication session).
```

`docker logs iso15118-secc-1 > ../captures/session-01-secc.log` to keep it.

⚠️ **Docker containers talk over plain Ethernet, so there is no SLAC and no
HomePlug GreenPHY layer here.** This validates layer 2 upwards only; the PLC
pairing that a real station does is not exercised.
⚠️ **容器之间走的是普通以太网，所以这里没有 SLAC，也没有 HomePlug GreenPHY 层。**
这只验证了第 2 层往上；真桩要做的 PLC 配对没有被覆盖。

### Alternative — a `veth` pair on the host / 备选: 宿主机上的 veth 对

Only needed if you want the two ends outside Docker (e.g. to `tcpdump` on the
host without entering a container namespace).
只有当你想让两端都跑在 Docker 外面时才需要(比如想在宿主机上直接 `tcpdump`，
不用进容器的网络命名空间)。

```bash
sudo ip link add veth-evcc type veth peer name veth-secc
sudo ip link set veth-evcc up
sudo ip link set veth-secc up
# ISO 15118 runs over IPv6 link-local — both ends need one.
# ISO 15118 跑在 IPv6 链路本地地址上 —— 两端都要有。
ip -6 addr show veth-evcc
```

> If IPv6 link-local addresses do not appear, `sysctl net.ipv6.conf.all.disable_ipv6`
> is probably `1`. This is the single most common reason a V2G lab does not start.
> 如果没出现 IPv6 链路本地地址，多半是 `net.ipv6.conf.all.disable_ipv6=1`。
> 这是 V2G 实验环境起不来的头号原因。
---

## Option B — EVerest (C++, the industrial one)
## 方案 B —— EVerest (C++，工业级)

```bash
git clone https://github.com/EVerest/everest-demo.git
cd everest-demo
# it ships docker compose files for a full charging stack
# 它自带完整充电栈的 docker compose 配置
```

EVerest is what a real product looks like: `EvseManager`, `EvseV2G`
(DIN 70121 + ISO 15118-2), an OCPP module, an energy manager, all connected
over MQTT. Even if you never modify it, **draw its module topology** — that
diagram alone is worth bringing to the interview.
EVerest 是真实产品的样子: `EvseManager`、`EvseV2G`(DIN 70121 + ISO 15118-2)、
OCPP 模块、能量管理器，全部通过 MQTT 互联。即使你一行都不改，
**把它的模块拓扑画出来** —— 光这张图就值得带进面试。

---

## Option C — pyPLC (understand the physical layer)
## 方案 C —— pyPLC (理解物理层)

```bash
git clone https://github.com/uhi22/pyPLC.git
cd pyPLC && cat doc/hardware.md
```

You do not need the hardware to learn from this repo. Read how SLAC works,
and read `pcapConverter.py` — you will want the same trick in project 04.
不需要硬件也能从这个仓库学到东西。读懂 SLAC 的工作方式，
并读 `pcapConverter.py` —— 项目 04 里你会想用同样的技巧。

---

## Capturing the traffic / 抓包

```bash
# V2G application traffic is TCP over IPv6; SDP discovery is UDP 15118.
# V2G 应用层是 IPv6 上的 TCP; SDP 发现用 UDP 15118。
sudo tcpdump -i veth-secc -w ../captures/session-01.pcap
wireshark ../captures/session-01.pcap
```

What you will see, bottom to top / 你会自下而上看到:

```
1. UDP  port 15118      SECC Discovery Protocol (SDP)  —— "SECC 在哪?"
2. TCP  handshake        (+ TLS if Plug & Charge)
3. V2GTP                8-byte header: version, payload type, length
4. EXI                  a binary blob — Wireshark cannot decode it
```

Step 4 is the wall everyone hits. That is what `OpenV2Gx` is for:
第 4 步是所有人都会撞上的墙。`OpenV2Gx` 就是干这个的:

```bash
git clone https://github.com/uhi22/OpenV2Gx.git
cd OpenV2Gx && make
./OpenV2G_v0.9.5 EE <hex-blob>     # check the repo README for the exact flags
```

---

## Injecting a fault / 注入故障

This is the part that turns "I ran a demo" into "I debugged a protocol".
这一步把"我跑了个 demo"变成"我调试了一个协议"。

Pick one / 三选一:

1. **Impossible power range** — make the SECC answer `ChargeParameterDiscoveryRes`
   with an `EVSEMaximumCurrentLimit` far below what the EV asked for. Watch how
   the EVCC reacts. / 让 SECC 在 `ChargeParameterDiscoveryRes` 里返回远低于车请求的
   电流上限，观察 EVCC 怎么反应。
2. **CableCheck timeout** — delay the SECC's `CableCheckRes` past the spec
   timeout. The EV should abort the session. Find the exact timeout in the
   standard and confirm the observed behaviour matches.
   把 `CableCheckRes` 延迟到超过规范超时。车应该中止会话。去标准里找到确切的超时值，
   确认观察到的行为与之相符。
3. **Wrong ResponseCode** — return `FAILED_SequenceError` at a random point and
   trace how far the session unwinds. / 在随机位置返回 `FAILED_SequenceError`，
   追踪会话如何回退。

Save the log from the broken run. **It is the input for project 04.**
保存故障运行的日志。**它就是项目 04 的输入。**
