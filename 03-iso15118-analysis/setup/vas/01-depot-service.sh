#!/usr/bin/env bash
# Step 2 of the value-added-service experiment: offer one, and answer for it.
# 增值服务实验的第二步: 提供一个服务，并为它作答。
#
# A bus depot needs to know which vehicle is in which bay, when it leaves and
# what charge it needs by then. ISO 15118-2 carries none of that and cannot be
# made to: its XSD is normative and EXI is schema-informed, so adding a field
# shifts every subsequent bit and the peer decodes garbage. There is no
# equivalent of OCPP's opaque data string either.
# 公交场站需要知道哪辆车停在哪个车位、几点走、走前要充到多少。ISO 15118-2 一个字段
# 都没有，而且加不了: XSD 是规范性的，EXI 又是 schema-informed，多一个字段会让后面
# 每一位都错位，对端解出来全是乱码。它也没有 OCPP 那种不透明字符串。
#
# The sanctioned route is a value-added service, and the standard reserved the
# slot itself: ServiceCategory "OtherCustom" with ServiceName
# "UseCaseInformation" (datatypes.py:141 and :150). Nothing here is a
# workaround — ISO 15118-2 anticipated exactly this.
# 官方路线是增值服务，而且标准自己留好了槽: ServiceCategory 的 "OtherCustom"
# 配 ServiceName 的 "UseCaseInformation"。这里没有任何取巧 ——
# ISO 15118-2 早就预见到了这种用法。
#
# Requires TLS, so run 00-enable-tls.sh first. [V2G2-422] permits VAS only over
# a secured connection and both ends enforce it: without TLS the SECC does not
# build the service list and the EVCC never asks for details.
# 需要先跑 00-enable-tls.sh。[V2G2-422] 规定增值服务只能在加密连接上用，
# 两端都照做了 —— 不开 TLS，桩不构造服务列表，车也不会去问详情。

set -euo pipefail

STACK="${STACK:-$HOME/codespace/iso15118}"
PATCH="$(cd "$(dirname "$0")" && pwd)/01-depot-service.patch"

[ -d "$STACK/.git" ] || { echo "stack not found at $STACK"; exit 1; }
cd "$STACK"

# Unlike the fault-injection scripts, this one patches the source and rebuilds
# rather than bind-mounting over the installed module. Three files change, and
# a mount per file is more fragile than one rebuild — the evcc service already
# carries a mount from 00-enable-tls.sh that a second volumes: block would
# silently replace.
# 和注入故障的脚本不同，这个改源码 + 重新构建，而不是挂载覆盖已安装的模块。
# 要改三个文件，逐个挂载比重建一次更脆 —— evcc 那边已经有 00-enable-tls.sh
# 加的挂载了，再写一个 volumes: 块会把它悄悄顶掉。
if git apply --check "$PATCH" 2>/dev/null; then
  git apply "$PATCH"
  echo "==> patch applied"
elif git apply --check --reverse "$PATCH" 2>/dev/null; then
  echo "==> patch is already applied, nothing to do"
else
  echo "patch does not apply — upstream has moved" >&2
  exit 1
fi

echo "==> rebuilding (the containers run the copy installed under /venv,"
echo "    not the source tree, so a rebuild is what makes the change real)"
make build

cat <<'NEXT'

Now capture a session / 现在抓一次会话:
  COMPOSE_EXTRA=~/codespace/iso15118/docker-compose.vas.yml \
    bash 03-iso15118-analysis/setup/capture-session.sh session-05-vas.pcap

Expect ServiceDetailReq{ServiceID: 4} and a ServiceDetailRes carrying four
typed parameters. 应该看到车问服务 4，桩回四个带类型的参数。

Revert / 撤销:
  cd ~/codespace/iso15118 && git apply --reverse 01-depot-service.patch && make build
NEXT
