# 01 · OCPP 1.6 Charge Point + Smart Charging
# 01 · OCPP 1.6 充电桩 + 智能充电

**Goal / 目标** — Build a charge point that speaks real OCPP 1.6-J, then make a
CSMS curtail it with `SetChargingProfile` and watch the delivered current drop.
构建一个真正会说 OCPP 1.6-J 的充电桩，然后让后台用 `SetChargingProfile` 对它限功率，
并亲眼看到输出电流被压下去。

**Why this matters / 为什么重要** — "How does a grid limit from the backend reach
the car?" is the single most likely technical question for this role. After this
stage you can answer it with your own code on screen.
"后台的电网限值是怎么传到车上的?"是这个岗位最可能被问到的技术题。做完这一阶段，
你可以用自己屏幕上的代码来回答。

---

## Quick start / 快速开始

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# terminal 1 — toy CSMS, pushes a curtailment profile 15 s after connect
# 终端 1 —— 玩具后台，连接后 15 秒下发一条限功率曲线
python -m csms.central_system --port 9000 --push-profile-after 15

# terminal 2 — the charge point
# 终端 2 —— 充电桩
python -m cp.charge_point --csms ws://localhost:9000 --id CP_1 --autostart 2
```

Run the tests / 运行测试:

```bash
python -m unittest discover -s tests -v     # 15 tests, no network needed
```

---

## What you should see / 你应该看到什么

```
csms   BootNotification from ev-charging-lab / SimCP-1
csms   StatusNotification c1: Available
csms   StatusNotification c1: Preparing
csms   Authorize(DEADBEEF) -> Accepted
csms   StartTransaction c1 idTag=DEADBEEF -> tx=1
csms   StatusNotification c1: Charging
csms   SetChargingProfile -> Accepted
csms   MeterValues c1: Power.Active.Import=7360.0W, Current.Import=32.0A   <- unconstrained
...
cp     t=   34s  EV wants 32.0A, profile caps at 16.0A -> delivering 16.0A (3.68 kW)
csms   MeterValues c1: Power.Active.Import=3680.0W, Current.Import=16.0A   <- curtailed
```

That single transition from 32 A to 16 A **is** smart charging.
从 32 A 到 16 A 的这一次跳变，**就是**智能充电本身。

---

## Layout / 目录结构

| Path | What it is | 说明 |
|---|---|---|
| `cp/state_machine.py` | Connector status model, no I/O | 接口状态机，无 I/O，纯逻辑 |
| `cp/profile.py` | ⭐ Charging-profile evaluation, no I/O | ⭐ 充电曲线求值，无 I/O，纯逻辑 |
| `cp/charge_point.py` | The OCPP client (asyncio + websockets) | OCPP 客户端 |
| `csms/central_system.py` | A toy CSMS to develop against | 用于开发的玩具后台 |
| `tests/` | 15 unit tests over the two pure modules | 覆盖两个纯逻辑模块的 15 个单元测试 |
| `docker-compose.yml` | SteVe, a real open-source CSMS | SteVe，真正的开源后台 |

The two pure modules are deliberately I/O-free. That is the design point worth
saying out loud in the interview: **protocol logic you can unit-test beats
protocol logic you can only observe over a socket.**
两个纯逻辑模块刻意不含 I/O。这个设计点值得在面试时主动讲出来:
**能做单元测试的协议逻辑，胜过只能靠抓包观察的协议逻辑。**

---

## Step by step / 分步推进

### Day 2 — get the loop running / 跑通闭环
- [ ] `pip install -r requirements.txt`, run both processes, read the raw
      JSON frames the `ocpp` library logs. Match every frame to the OCPP 1.6
      spec. / 跑起来，读 `ocpp` 库打印的原始 JSON 帧，逐条对照 OCPP 1.6 规范。
- [ ] Change `--autostart 0` and drive the transaction with
      `--remote-start-after 5` on the CSMS instead. / 改用后台远程启动。
- [ ] Break something on purpose: change the id tag to `BADCAFE` and watch
      `Authorize -> Invalid` abort the flow. / 故意搞坏: 把 idTag 改成
      `BADCAFE`，观察鉴权失败如何中止流程。

### Day 3 — the pure logic / 纯逻辑
- [ ] Read `cp/profile.py` end to end, then implement **TODO 1 (Recurring)**.
- [ ] Implement **TODO 2 (W ↔ A conversion)** and write the test for it.
- [ ] Implement **TODO 3 (minChargingRate → SuspendedEVSE)**.
- [ ] Extend `cp/state_machine.py` with the error-code latch (TODO 1).

### Day 4 — make it look like a product / 让它像个产品
- [ ] Bring up SteVe (`docker compose up -d`), register `CP_1` in the web UI,
      point the charge point at it. / 起 SteVe，在 Web UI 注册 `CP_1`，接上去。
- [ ] Trigger `RemoteStartTransaction` from SteVe's UI. / 从 SteVe 界面远程启动。
- [ ] Implement offline queueing (`charge_point.py` TODO 4): kill the CSMS
      mid-transaction and prove nothing is lost on reconnect.
      实现离线排队: 事务中途杀掉后台，证明重连后数据不丢。
- [ ] Save one full session log — you need it as input for project **04**.
      保存一份完整会话日志 —— 项目 **04** 要用它当输入。

---

## Extending OCPP without breaking it / 在不破坏 OCPP 的前提下扩展它

A bus depot needs to know which vehicle is in which bay, when it has to leave,
and what state of charge it needs by then. None of those fields exist in OCPP,
and none of them should: they are fleet-operations data, the departure time
comes from the operator's scheduling system rather than from the charger, and a
protocol serving hundreds of millions of passenger cars has no business carrying
a field that a few thousand depots need.
公交场站需要知道哪辆车停在哪个车位、几点要走、走之前要充到多少电。这些字段
OCPP 里一个都没有，也不该有: 它们属于车队调度，发车时间来自运营方的排班系统
而不是充电桩，而一个服务几亿辆乘用车的协议，不该为几千个场站携带字段。

`DataTransfer` is the sanctioned way in — implemented here in both directions,
because a depot genuinely needs both. The station knows which bay is occupied;
only the backend knows which vehicle was rostered to it.
`DataTransfer` 是官方认可的入口 —— 这里两个方向都实现了，因为场站确实两边都需要。
桩知道哪个车位有车，只有后台知道排的是哪辆车。

```
CP  -> CSMS   de.example.depot / DepotSlotStatus       slot, busId, departAt, targetSoc, transactionId
CSMS -> CP    de.example.depot / DepotSlotAssignment   ... plus idTag
```

**`vendorId` is not `chargePointVendor`.** The latter says who built the
hardware and is sent once at boot; the former namespaces the *vocabulary* of one
message, and a single station can speak several — its manufacturer's, the
operator's, a roaming platform's. It does the same job for OCPP that a URN does
for an XSD.
**`vendorId` 不是 `chargePointVendor`。** 后者说这台硬件是谁造的、开机时发一次；
前者是单条消息所用**词汇**的命名空间，一台桩可以同时说好几种 ——
厂商的、运营方的、漫游平台的。它对 OCPP 起的作用，和 URN 对 XSD 是同一件事。

Both ends use all four status codes rather than collapsing everything into
`Rejected`, because they point at different fixes:
两端都用满了四个状态码，而不是把一切都塞进 `Rejected`，因为它们指向不同的修法:

| Status | Meaning | What to fix |
|---|---|---|
| `UnknownVendorId` | nobody here speaks that namespace | wire up the extension, or check the spelling |
| `UnknownMessageId` | namespace is right, this message is not implemented | implement it, or stop sending it |
| `Rejected` | understood, but this payload is not acceptable | look at the payload |
| `Accepted` | processed | — |

⚠️ **The failure mode is silence.** The CALL goes out, a CALLRESULT comes back,
and the sender's log reads as success — but `UnknownVendorId` means the peer
never processed it and the feature simply does nothing. Project 04's rule
**R012** exists to catch exactly that; no other rule could, because R002 needs a
CALLERROR frame and R003's status list never included it.
⚠️ **它的失败方式是静默。** 请求发出去了、响应也回来了、发送方日志一切正常 ——
但 `UnknownVendorId` 意味着对端根本没处理，功能就是没生效。项目 04 的 **R012**
就是为抓这个而写的；别的规则抓不到，因为 R002 要 CALLERROR 帧，
而 R003 的状态清单里从来没有它。

⚠️ **`data` is a string, not an object.** OCPP declares it that way on purpose —
typing it would mean defining a schema for content OCPP cannot know — so the
payload is JSON inside JSON, and the backend cannot validate it. ISO 15118's
`ParameterSet` makes the opposite trade: typed values the peer can check, at the
cost of only six types. Two answers to the same question, for two different
trust relationships.
⚠️ **`data` 是字符串不是对象。** OCPP 故意这么定 —— 给它定类型就等于要为
OCPP 无法预知的内容定义 schema —— 于是载荷是 JSON 套 JSON，后台没法校验。
ISO 15118 的 `ParameterSet` 做了相反的取舍: 带类型、对端能校验，代价是只有六种类型。
同一个问题的两种答案，对应两种不同的信任关系。

---

## Gotchas you will hit / 你一定会踩的坑

| Symptom | Cause | 中文 |
|---|---|---|
| `malformed charging profile: 'chargingSchedule'` | The `ocpp` library snake_cases every nested payload key before your handler sees it. `chargingSchedule` becomes `charging_schedule`. | `ocpp` 库会把嵌套 payload 的所有键转成 snake_case 再交给你的 handler。`profile.parse()` 用 `_get()` 同时兼容两种风格 —— 这正是为什么它存在。 |
| CSMS logs an empty `MeterValues` line | Same cause: `sampledValue` → `sampled_value`. | 同一个原因。 |
| `websockets` handshake fails | The subprotocol must be negotiated as `ocpp1.6` on **both** sides. | 两端都必须协商 `ocpp1.6` 子协议。 |
| SteVe rejects BootNotification | The charge point id must be pre-registered in SteVe. | 桩 id 必须先在 SteVe 里注册。 |

---

## Talking points for the interview / 面试话术

1. **"I implemented OCPP smart charging."** Then explain profile *purpose*
   precedence and why `ChargePointMaxProfile` is a ceiling, not a winner.
   讲清 purpose 优先级，以及为什么 `ChargePointMaxProfile` 是上限而不是"赢家"。
2. **"I kept protocol logic free of I/O so it is unit-testable."** Show
   `test_step_boundaries` — the off-by-one at `startPeriod` is a real bug class.
   展示 `test_step_boundaries` —— `startPeriod` 的边界差一错误是真实的 bug 类型。
3. **"OCPP 1.6 vs 2.0.1."** 2.0.1 replaces Start/StopTransaction with a single
   `TransactionEvent`, adds a formal device model, and adds ISO 15118
   Plug & Charge certificate messages. Mentioning the *reason* for the merge
   (transaction state was ambiguous in 1.6) shows you read the spec, not a blog.
   2.0.1 用单一 `TransactionEvent` 取代 Start/StopTransaction，加入正式设备模型
   和 ISO 15118 Plug & Charge 证书消息。说出合并的**原因**(1.6 里事务状态有歧义)
   才能证明你读的是规范而不是博客。

---

## References / 参考

- [mobilityhouse/ocpp](https://github.com/mobilityhouse/ocpp) — the library used here
- [ocpp.readthedocs.io](https://ocpp.readthedocs.io/) — API docs
- [steve-community/steve](https://github.com/steve-community/steve) — OCPP 1.6 CSMS
- [citrineos/citrineos](https://github.com/citrineos/citrineos) — OCPP 2.0.1 CSMS
- [SAP/e-mobility-charging-stations-simulator](https://github.com/SAP/e-mobility-charging-stations-simulator) — load-test with many stations
