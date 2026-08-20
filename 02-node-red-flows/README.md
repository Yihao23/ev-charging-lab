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

`flows-dashboard.json` requires `node-red-dashboard` and was **not** run —
it is a skeleton for you to finish (see below).
`flows-dashboard.json` 需要 `node-red-dashboard`，**未**实际运行 ——
它是留给你完成的骨架(见下)。

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
- [ ] Add an alarm path: if `status == "Faulted"` or a `CALLERROR` arrives,
      light a red indicator and append to an alarm log.
      加告警支路: 出现 `Faulted` 或 `CALLERROR` 时点亮红灯并写告警日志。
- [ ] Add an automated scenario: on a button press, run
      RemoteStart → wait 30 s → curtail to 6 A → wait 30 s → RemoteStop,
      and assert the station followed. That is a **test case**, not a demo.
      加自动化场景: 一键跑完 启动 → 等 30s → 限流 6A → 等 30s → 停止，
      并断言桩确实照做了。这是**测试用例**，不是 demo。
- [ ] 📸 **Take the screenshot.** Save it to `screenshots/`. It goes in the
      top-level README. / **截图**，存到 `screenshots/`，放进顶层 README。

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
