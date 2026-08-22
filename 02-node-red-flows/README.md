# 02 · Node-RED Mock CSMS + Dashboard
# 02 · Node-RED 模拟后台 + 仪表盘

**Goal / 目标** — Build a working OCPP 1.6 backend out of Node-RED nodes, drive a
real charge point from it, and visualise the session live.
用 Node-RED 节点搭一个能用的 OCPP 1.6 后台，用它驱动真实的充电桩，并实时可视化会话。

**Why Node-RED is in the job description / 为什么 JD 里会有 Node-RED** — In an
e-mobility R&D team it is the glue for test benches: wire a simulated station,
a simulated car, a power supply and a meter together in an afternoon; mock a
backend you do not want to boot; bridge OCPP ↔ MQTT ↔ Modbus ↔ CAN.
在 E-Mobility 研发团队里，它是测试台架的胶水: 一个下午就能把模拟桩、模拟车、
电源和电表串起来; 模拟一个你不想启动的后台; 在 OCPP ↔ MQTT ↔ Modbus ↔ CAN
之间做桥接。

**Highest visual return per hour of the whole lab.** One screenshot of a live
dashboard says more in an interview than three pages of code.
**整个实验室里单位时间视觉回报最高的项目。** 面试时一张实时仪表盘截图，
胜过三页代码。

---

## Quick start / 快速开始

```bash
cd 02-node-red-flows
docker compose up -d                       # or: npx node-red
# open http://localhost:1880  ->  menu  ->  Import  ->  flows.json
```

Then point the charge point from project 01 at Node-RED:
然后把项目 01 的充电桩接到 Node-RED:

```bash
cd ../01-ocpp-charge-point
python -m cp.charge_point --csms ws://localhost:1880/ocpp --id CP_1 --autostart 1
```

Click the inject buttons in the editor to curtail the station live.
在编辑器里点 inject 按钮，实时对充电桩限功率。

---

## Verified / 已验证

This flow was imported into Node-RED and run end to end against
`01-ocpp-charge-point`. Node-RED reported no missing node types.
本流程已导入 Node-RED 并与 `01-ocpp-charge-point` 端到端跑通，
Node-RED 未报告任何缺失节点类型。

Clicking **⚡ curtail to 6 A** produced, on the station side:
点击 **⚡ curtail to 6 A** 后，桩侧输出:

```
cp  t=   14s  EV wants 32.0A, profile caps at 6.0A -> delivering 6.0A (1.38 kW)
```

and in `out/ocpp-session.jsonl`:

```json
{"ts":"…","type":2,"action":"MeterValues","status":"Charging","tx":1,
 "power_w":1380,"current_a":6,"energy_wh":…}
```

`flows-dashboard.json` requires `node-red-dashboard` (the 3.x package, not
`@flowfuse/node-red-dashboard` — Dashboard 2.0 renames every node type and
this file will not import). It runs: the screenshot below is a full session
driven from `/ui` alone.
`flows-dashboard.json` 需要 `node-red-dashboard`(3.x 那个包, 不是
`@flowfuse/node-red-dashboard` —— Dashboard 2.0 把每个节点类型都改名了,
这个文件导入不进去)。它是能跑的: 下面那张截图里的完整会话全程只用 `/ui` 操作。

![The bench dashboard after a passing test run](screenshots/dashboard.png)

The power trace is the test itself: idle, start, curtail to 6 A, stop. The
three PASS lines beside it are `run scenario` reporting what it observed at
each step.
功率曲线就是这次测试本身: 空闲、启动、限流到 6 A、停止。旁边三行 PASS 是
`run scenario` 报告它在每一步观察到的结果。

---

## What is in `flows.json` / `flows.json` 里有什么

Core nodes only — **no extra packages to install**, so it imports into a stock
Node-RED and cannot fail with "unknown node type".
只用核心节点 —— **不需要装任何额外包**，所以能导入原版 Node-RED，
不会出现 "unknown node type"。

```
websocket in ──► decode OCPP-J frame ──┬──► build CALLRESULT ──► websocket out
   /ocpp/CP_1                          │
                                       └──► extract telemetry ──┬──► debug
                                                                ├──► session.jsonl
                                                                └──► link out ──► dashboard

inject ▶ RemoteStartTransaction ──► RemoteStart ─────┐
inject ⏹ RemoteStopTransaction  ──► RemoteStop ──────┼──► websocket out
inject ⚡ 6 A / 16 A / 32 A      ──► SetChargingProfile ┘
```

| Node | What it does | 说明 |
|---|---|---|
| `decode OCPP-J frame` | `[2,uid,action,payload]` → `msg.ocpp`, and correlates our own requests with their replies | 解帧，并把我们发出的请求与对方响应关联 |
| `build CALLRESULT` | Answers Boot / Heartbeat / Status / Authorize / Start / Stop / MeterValues; replies `CALLERROR NotImplemented` to anything else | 回应各类请求；未知消息回 `CALLERROR` |
| `extract telemetry` | Flattens any frame into one record with `power_w` / `current_a` / `energy_wh` | 把任意帧压平成一条含功率/电流/电量的记录 |
| `SetChargingProfile` | ⭐ Builds the curtailment message; `msg.limit` lets one node serve three buttons | ⭐ 构造限功率消息；用 `msg.limit` 让一个节点服务三个按钮 |

**The detail worth pointing at in an interview:** the default branch of
`build CALLRESULT` never stays silent — it returns `CALLERROR NotImplemented`.
An unanswered CALL trips the station's 30 s message timeout and drops the whole
session, and "the station randomly disconnects" is a genuinely hard bug to
diagnose from the field.
**面试时值得指出的细节:** `build CALLRESULT` 的 default 分支绝不沉默 ——
它返回 `CALLERROR NotImplemented`。没有响应的 CALL 会触发桩侧 30 秒超时并断开
整个会话，而"桩会随机掉线"是现场极难诊断的 bug。

---

## Gotchas / 坑

| Symptom | Cause | 中文 |
|---|---|---|
| Station connects but nothing happens | The listener path must match exactly. `flows.json` listens on `/ocpp/CP_1`, so the station must use `--csms ws://host:1880/ocpp --id CP_1`. | 监听路径必须完全一致 |
| Reply goes to the wrong client | The `websocket out` node targets `msg._session`. Injected messages have no `_session`, so they broadcast to every client on that listener — fine for one station, wrong for many. | inject 出来的消息没有 `_session`，会广播给该监听器上所有客户端 |
| A commercial station refuses to connect | Node-RED does not negotiate the `ocpp1.6` WebSocket subprotocol. The Python client in project 01 tolerates that; **real stations usually do not.** Node-RED is a bench tool, not a CSMS. | Node-RED 不协商 `ocpp1.6` 子协议。项目 01 的 Python 客户端能容忍，**真实充电桩通常不能。** Node-RED 是台架工具，不是后台产品 |
| The `file` node writes nowhere you can see | Inside Docker it writes to the container's `/tmp`. The compose file maps `./out:/tmp` so the log lands in `out/`. | 容器里的 `/tmp` 已映射到 `out/` |

---

## Step by step / 分步推进

### Day 5 — get it running / 跑起来
- [ ] Import `flows.json`, connect the station, watch the debug sidebar.
- [ ] Click each inject button and confirm the station reacts.
      逐个点 inject 按钮，确认桩有反应。
- [ ] Read `decode OCPP-J frame` line by line. Every OCPP-J frame is one of
      three shapes — make sure you can name all three from memory.
      逐行读解码节点。OCPP-J 只有三种帧形，确保你能默写出来。

### Day 6 — the dashboard / 仪表盘
- [ ] `npm install node-red-dashboard` (or add it in Manage Palette).
- [ ] Import `flows-dashboard.json`, then wire the `telemetry ->` link out in
      `flows.json` to the `telemetry <-` link in. / 把两个 link 节点连起来。
- [ ] Open `http://localhost:1880/ui`. You should see the connector status,
      a current gauge and a live power chart.
- [ ] Finish the TODO: add three `ui_button` nodes in the *Operator* group so
      the dashboard replaces the inject buttons.
      完成 TODO: 在 Operator 分组加三个 `ui_button`，用仪表盘按钮取代 inject。

### Day 7 — make it a test bench / 变成测试台架
- [X] Add an alarm path: if `status == "Faulted"` or a `CALLERROR` arrives,
      light a red indicator and append to an alarm log.
      加告警支路: 出现 `Faulted` 或 `CALLERROR` 时点亮红灯并写告警日志。
- [X] Add an automated scenario: on a button press, run
      RemoteStart → wait 30 s → curtail to 6 A → wait 30 s → RemoteStop,
      and assert the station followed. That is a **test case**, not a demo.
      加自动化场景: 一键跑完 启动 → 等 30s → 限流 6A → 等 30s → 停止，
      并断言桩确实照做了。这是**测试用例**，不是 demo。
- [X] 📸 **Take the screenshot.** Save it to `screenshots/`. It goes in the
      top-level README. / **截图**，存到 `screenshots/`，放进顶层 README。

---

## What was built on top / 在骨架之上加了什么

The flow shipped in this repo was a decode-and-reply skeleton. Three things
were added while working through days 5 to 7, and each one came out of
watching the bench misbehave rather than from a list.
仓库里原本的流程只是"解码并回复"的骨架。Day 5–7 期间加了三样东西，
每一样都不是照着清单做的，而是看着台架出问题才加的。

### Alarm routing / 告警分流

`Faulted?` is a `switch` on `msg.payload.status`. The Faulted branch writes
to `alarms.jsonl` and lights a red field on the dashboard; the `otherwise`
branch clears that field.
`Faulted?` 是一个按 `msg.payload.status` 分流的 switch。故障那一路写
`alarms.jsonl` 并点亮仪表盘上的红字, `otherwise` 那一路把红字清掉。

The second branch is the part worth arguing for. Without it the station
recovers, `Status` returns to `Available`, and the alarm keeps saying
`GroundFailure` — two fields on the same panel contradicting each other. An
alarm that never clears stops being read.
第二条分支才是值得辩护的部分。没有它, 桩恢复之后 `Status` 变回 `Available`,
而告警仍然写着 `GroundFailure` —— 同一块面板上两个字段互相矛盾。
一个永不消失的告警很快就没人看了。

`extract telemetry` had to start carrying `error_code`, guarded so it is set
only for StatusNotification. Attaching it unconditionally would stamp a
fabricated `NoError` onto MeterValues, which carry no error code at all.
为此 `extract telemetry` 要开始带上 `error_code`, 并且只在 StatusNotification
时才设置。无条件附加会给 MeterValues 盖上一个伪造的 `NoError` ——
那类消息根本不报告错误码。

### An operator panel, not an editor / 运维面板，而不是编辑器

Driving the bench used to mean opening the Node-RED editor and clicking the
squares on inject nodes. That editor can also delete nodes and break the
flow, so it is not something to hand to whoever is running a test. Four
`ui_button`s — Start, 6 A, Stop, Run test — now do the same job from `/ui`,
wired back through `link` nodes to the functions that already existed. The
inject nodes still work; nothing was moved.
以前驱动台架要打开 Node-RED 编辑器、点 inject 节点上的小方块。那个编辑器
同时也能删节点、把流程搞坏, 不该交给跑测试的人。现在 `/ui` 上四个
`ui_button` —— Start / 6 A / Stop / Run test —— 通过 link 节点接回已有的
function 做同样的事。inject 节点仍然可用, 什么都没有搬走。

`SetChargingProfile` reads `msg.limit`, but a `ui_button` sends
`msg.payload`, so the 6 A button would have silently curtailed to the 16 A
default. The function falls back to `msg.payload`, which keeps both entry
points working without a `change` node in between.
`SetChargingProfile` 读的是 `msg.limit`, 而 `ui_button` 发的是 `msg.payload`,
所以 6 A 按钮本来会静默地限到默认的 16 A。让该函数回退到 `msg.payload`,
两个入口就都能用, 中间也不需要插一个 `change` 节点。

### One-button test with assertions / 带断言的一键测试

`run scenario` sends RemoteStart, waits, curtails to 6 A, waits, sends
RemoteStop, and checks the delivered current at each step. It reports PASS
or FAIL per step on the dashboard and colours its own node status in the
editor.
`run scenario` 依次发出启动、等待、限流到 6 A、等待、停止, 并在每一步检查
实际输出的电流。它在仪表盘上逐条报告 PASS / FAIL, 并给编辑器里自己的节点
状态着色。

A command is not a result. Node-RED wires are one-way deliveries with no
return value, so `run scenario` cannot know whether RemoteStart worked. The
only way to find out is to observe what the station reports back — which is
exactly what makes this a test rather than a macro.
**指令不等于结果。** Node-RED 的连线是单向投递、没有返回值, 所以
`run scenario` 无从知道 RemoteStart 是否成功。唯一的办法是观察桩上报了什么
—— 这正是它算"测试"而不是"宏"的原因。

Each assertion sits 15 s behind its command. The station has to receive it,
apply it, and reach its next 5 s metering tick before the change is
observable; asserting immediately would always fail.
每条断言都落后它的指令 15 秒。桩要收到、执行、并等到下一个 5 秒计量周期,
变化才观察得到; 紧跟着断言必然失败。

`setTimeout` outlives the node, so the pending timers are collected and
cleared in **On Stop**. A redeploy mid-run would otherwise leave them firing
into a node that no longer exists.
`setTimeout` 的寿命长于节点, 所以待触发的定时器被收集起来, 在 **On Stop**
里清掉。否则测试跑到一半重新 Deploy, 它们会向一个已经不存在的节点发消息。

### What the test caught on its first run / 测试第一次跑就抓到的东西

`FAIL  stopped  expected=0 got=6`

Stopping a transaction stops MeterValues, so `flow.lastCurrent` kept the
last reading it ever saw. The gauge showed 0 because `split to widgets`
zeroes it for display — but that zeroing lived in the presentation layer,
not where the state is derived. The dashboard looked right and the state
underneath was wrong.
停止事务之后桩不再发 MeterValues, 于是 `flow.lastCurrent` 一直停在它见过的
最后一个读数上。仪表盘显示 0, 是因为 `split to widgets` 为了显示把它归了零
—— 但那段归零逻辑活在**展示层**, 而不是状态派生的地方。界面是对的,
底下的状态是错的。

Nobody was going to find that by looking. It is the fourth instance of the
same shape in this flow: the gauge, the power trace, the alarm text, and now
the state a test reads. Every value that can change needs a path that clears
it, not just one that sets it.
光靠看是发现不了的。这已经是同一形状的问题在本流程里第四次出现: 仪表盘、
功率曲线、告警文字, 现在是测试读的那个状态。**每一个会变化的值, 都需要一条
把它清掉的路径, 而不只是一条设置它的路径。**

### Known limits / 已知局限

- Fixed delays, not polling. A step that is merely slow fails the same way
  a broken one does. Waiting until a value changes, with a timeout, would
  be both faster and more honest.
  用的是固定延时而不是轮询。一个只是慢了的步骤, 失败方式和真正坏掉的一样。
  改成"等到值改变、超时为止"会更快也更诚实。
- `lastCurrent` now means "current current". The name says otherwise and
  should follow.
  `lastCurrent` 现在的含义是"当前电流", 名字却在说别的, 该改。
- A CSMS can never know the actual current, only the last one reported. A
  station that goes offline mid-charge leaves the backend holding a stale
  number with no way to tell. Pairing every value with the time it was
  taken would at least make the staleness visible.
  后台永远不可能知道真实电流, 只能知道最后一次上报的值。桩在充电途中掉线,
  后台手里就是一个陈旧的数字, 而且无从判断。给每个值配上"采样时刻"至少能
  让陈旧变得可见。

---

## Talking points / 面试话术

1. **"I built a mock CSMS in Node-RED to drive bench tests."** Then explain why
   a mock beats a real backend on a test bench: you can make it misbehave on
   purpose. / 解释为什么台架上模拟后台胜过真后台: 你可以让它按需犯错。
2. **"It never leaves a CALL unanswered."** Explain the 30 s timeout and the
   "station randomly disconnects" failure mode.
3. **"Node-RED is a bench tool, not a product."** Naming the subprotocol
   limitation yourself shows judgement, not ignorance.
   主动说出子协议这个限制，体现的是判断力而不是无知。

---

## References / 参考

- [Argonne-National-Laboratory/node-red-contrib-ocpp](https://github.com/Argonne-National-Laboratory/node-red-contrib-ocpp) — ready-made OCPP 1.5/1.6 nodes, if you would rather not hand-roll the decode
- [@anl-ioc/node-red-contrib-ocpp2](https://www.npmjs.com/package/@anl-ioc/node-red-contrib-ocpp2) — the OCPP 2.0.1 version
- [node-red-dashboard](https://flows.nodered.org/node/node-red-dashboard) — the UI nodes used by `flows-dashboard.json`
