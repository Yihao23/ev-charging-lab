#!/usr/bin/env bash
# Step 1 of the value-added-service experiment: turn TLS on.
# 增值服务实验的第一步: 打开 TLS。
#
# Not optional. ISO 15118-2 [V2G2-422] allows value-added services only over a
# TLS-secured connection, and both ends of EcoG's stack enforce it:
#
#   secc/states/iso15118_2_states.py:367   if self.comm_session.is_tls:
#       -> the service list is not even built without TLS
#   evcc/states/iso15118_2_states.py:295   if not self.comm_session.is_tls ...:
#       -> the car never sends a ServiceDetailReq without TLS
#
# 不是可选项。ISO 15118-2 的 [V2G2-422] 规定增值服务只允许在 TLS 连接上使用，
# EcoG 协议栈两端都照做了: 不开 TLS，桩根本不构造服务列表，车也根本不会去问详情。
#
# Both switches have to move together. evcc/comm_session_handler.py:504 aborts
# the session when the SECC signals TLS and the EVCC is configured without it.
# 两个开关必须一起动。SECC 报 TLS 而 EVCC 没配的话，会话会被直接中止。
#
# Nothing in the upstream clone is modified: patched copies are written to the
# stack directory and bind-mounted over the originals.
# 上游 clone 一个字节都不改: 改过的副本写到协议栈目录，挂载覆盖原文件。

set -euo pipefail

STACK="${STACK:-$HOME/codespace/iso15118}"
EVCC_CONFIG="iso15118/shared/examples/evcc/iso15118_2/evcc_config_eim_ac.json"
OUT="$STACK/vas-evcc-config.json"
OVERRIDE="$STACK/docker-compose.vas.yml"

[ -f "$STACK/$EVCC_CONFIG" ] || { echo "EVCC config not found"; exit 1; }

python3 - "$STACK/$EVCC_CONFIG" "$OUT" <<'PY'
import json, sys
src, out = sys.argv[1], sys.argv[2]
cfg = json.load(open(src, encoding="utf-8"))
if cfg.get("useTls") is True:
    sys.exit("useTls is already true — nothing to patch")
cfg["useTls"] = True
json.dump(cfg, open(out, "w", encoding="utf-8"), indent=2)
print(f"  patched -> {out}  (useTls: false -> true)")
PY

# environment: entries win over env_file, so SECC_ENFORCE_TLS is overridden here
# rather than by editing .env.dev.docker.
# compose 的 environment: 优先级高于 env_file，所以在这里覆盖，不改 .env.dev.docker。
cat > "$OVERRIDE" <<YML
services:
  secc:
    environment:
      SECC_ENFORCE_TLS: "True"
  evcc:
    volumes:
      - $OUT:/usr/src/app/$EVCC_CONFIG:ro
YML
echo "  override -> $OVERRIDE  (SECC_ENFORCE_TLS=True)"
echo
echo "Capture a TLS session / 抓一次 TLS 会话:"
echo "  COMPOSE_EXTRA=$OVERRIDE bash $(dirname "$0")/../capture-session.sh session-04-tls.pcap"
echo
echo "Revert / 撤销:  rm $OUT $OVERRIDE"
