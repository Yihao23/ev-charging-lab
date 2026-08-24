#!/usr/bin/env bash
# Fault 01 — the station offers less current than the EV says it can accept.
# 故障 01 —— 桩给出的电流低于车声明的最小值。
#
# In session-02 the car asked for EVMaxCurrent=32 A and EVMinCurrent=10 A, and
# the station answered EVSEMaxCurrent=32 A. This drops the station's answer to
# 5 A — below the car's stated minimum. ISO 15118-2 gives the EV a minimum for
# exactly this reason: below it, an onboard charger cannot regulate and should
# refuse rather than draw an unstable current.
# session-02 里车报 EVMaxCurrent=32 A、EVMinCurrent=10 A，桩答 EVSEMaxCurrent=32 A。
# 这里把桩的回答降到 5 A —— 低于车声明的最小值。ISO 15118-2 让车报最小值正是为此:
# 低于它，车载充电机无法稳定调节，应该拒绝而不是硬拉。
#
# Nothing in the repo is modified: a patched copy of simulator.py is written to
# the stack directory and bind-mounted over the installed module, so reverting
# is `rm` plus a restart.
# 仓库里什么都不改：把改过的 simulator.py 写到协议栈目录，再挂载覆盖已安装的模块，
# 撤销只需 rm 加重启。

set -euo pipefail

STACK="${STACK:-$HOME/codespace/iso15118}"
SRC="$STACK/iso15118/secc/controller/simulator.py"
OUT="$STACK/fault01-simulator.py"
OVERRIDE="$STACK/docker-compose.fault01.yml"
TARGET="/venv/lib/python3.10/site-packages/iso15118/secc/controller/simulator.py"

[ -f "$SRC" ] || { echo "simulator.py not found at $SRC"; exit 1; }

# get_ac_charge_params_v2() builds the AC_EVSEChargeParameter that goes into
# ChargeParameterDiscoveryRes. Only the current changes; nominal voltage and
# the SAScheduleList PMax stay as they were, so the experiment has one variable.
# get_ac_charge_params_v2() 构造 ChargeParameterDiscoveryRes 里的
# AC_EVSEChargeParameter。只改电流，标称电压和 SAScheduleList 的 PMax 不动，
# 保证实验只有一个变量。
python3 - "$SRC" "$OUT" <<'PY'
import re, sys
src, out = sys.argv[1], sys.argv[2]
text = open(src, encoding="utf-8").read()
old = """        evse_max_current = PVEVSEMaxCurrent(
            multiplier=0, value=32, unit=UnitSymbol.AMPERE
        )"""
new = """        evse_max_current = PVEVSEMaxCurrent(
            multiplier=0, value=5, unit=UnitSymbol.AMPERE  # FAULT 01: was 32
        )"""
if old not in text:
    sys.exit("anchor not found — did upstream change get_ac_charge_params_v2()?")
open(out, "w", encoding="utf-8").write(text.replace(old, new, 1))
print(f"  patched -> {out}")
PY

cat > "$OVERRIDE" <<YML
services:
  secc:
    volumes:
      - $OUT:$TARGET:ro
YML
echo "  override -> $OVERRIDE"
echo
echo "Now capture the broken session / 现在抓故障会话:"
echo "  COMPOSE_EXTRA=$OVERRIDE bash $(dirname "$0")/../capture-session.sh session-03-fault.pcap"
echo
echo "Revert / 撤销:"
echo "  rm $OUT $OVERRIDE"
