# Glossary / 术语表

Every acronym in this repo, with the one sentence that actually explains it.
本仓库出现的所有缩写，配一句真正能解释清楚的话。

---

## The two protocols / 两个协议

| Term | Full name | What it is | 中文 |
|---|---|---|---|
| **ISO 15118** | *Road vehicles — Vehicle to grid communication interface* | The language between **car and charging station** | **车与桩**之间的语言 |
| **OCPP** | Open Charge Point Protocol | The language between **charging station and backend** | **桩与后台**之间的语言 |

They do not overlap. The station speaks both, and translates between them.
两者不重叠。桩两种都说，并在中间做翻译。

---

## Roles / 角色

| Term | Where | Job | 中文 |
|---|---|---|---|
| **EVCC** | in the car | Negotiates on the car's behalf: how much power, current SoC, when it must be done | 代表车谈判 |
| **SECC** | in the station | Negotiates on the station's behalf: available power, authorisation, start/stop, metering | 代表桩谈判 |
| **vSECC** | in the station | A **software/virtualised SECC**. Two readings: (a) a product-grade SECC software stack — Vector ships one under this exact name, covering ISO 15118 + DIN 70121 + CHAdeMO + OppCharge + MCS; (b) a simulated SECC for bench testing with no hardware. Worth asking which one a company means. | **软件化的 SECC**。两种含义: (a) 产品级 SECC 软件栈; (b) 无硬件的仿真 SECC。值得在面试时主动问清对方指哪个 |
| **CSMS** | backend | Charging Station Management System — authorisation, billing, remote control, load management | 充电站管理系统 |
| **CPO** | operator | Charge Point Operator — runs the stations | 充电桩运营商 |
| **MO / eMSP** | operator | Mobility Operator / e-Mobility Service Provider — has the contract with the driver | 移动出行服务商，与车主签合同 |

---

## ISO 15118 internals / ISO 15118 内部

| Term | Meaning | 中文 |
|---|---|---|
| **PLC** | Power Line Communication — data ridden on top of the Control Pilot wire | 电力线通信，数据叠加在控制导引线上 |
| **HomePlug Green PHY** | The specific PLC modulation ISO 15118 uses | ISO 15118 使用的具体 PLC 调制方式 |
| **CP** | Control Pilot — the signalling wire in the connector; carries a PWM square wave (IEC 61851) and, on top of it, the PLC carrier | 控制导引线，既载 PWM 方波也载 PLC 载波 |
| **SLAC** | Signal Level Attenuation Characterization — measures signal attenuation to decide **which car is on which cable** in a car park where PLC signals leak between bays | 信号衰减特性测量 —— 在信号会串扰的停车场里判断**哪台车插在哪根线上** |
| **SDP** | SECC Discovery Protocol — the car multicasts "where is the SECC?" over UDP; the station replies with its IPv6 address and port | 车用 UDP 组播问"SECC 在哪"，桩回自己的 IPv6 地址和端口 |
| **V2GTP** | V2G Transfer Protocol — an 8-byte header (version, payload type, length) in front of every message | 每条消息前的 8 字节头 |
| **EXI** | Efficient XML Interchange — W3C binary XML. Messages are defined in XSD but sent as a schema-compressed binary blob, because `CurrentDemand` runs every ~250 ms over a low-bandwidth link | W3C 的二进制 XML。消息用 XSD 定义但按 schema 压缩后二进制传输 |
| **DIN SPEC 70121** | The DC-only predecessor/subset of ISO 15118-2. **Most deployed DC chargers actually negotiate this.** A real SECC supports both and falls back | ISO 15118-2 的直流前身/子集。**现网大多数直流桩实际协商的是它。** 真实 SECC 两个都支持并自动回退 |
| **Plug & Charge** | Plug in and charging is authorised automatically — no app, no card. Backed by a PKI: V2G Root CA → MO chain → contract certificate in the car | 插枪即自动鉴权计费。背后是一套 PKI 证书链 |
| **BPT** | Bidirectional Power Transfer (ISO 15118-20) — the car can feed back to the building or grid | 双向功率传输，车可以反向放电 |
| **MCS** | Megawatt Charging System — for trucks and buses | 兆瓦充电，用于卡车和公交 |
| **OppCharge** | Pantograph-based opportunity charging for buses | 基于受电弓的公交机会充电 |
| **CCS** | Combined Charging System — the connector family ISO 15118 runs on in Europe/North America | 欧美的组合充电连接器标准 |

### ISO 15118-2 DC message order / 消息顺序

```
supportedAppProtocol   ISO 15118-2 or DIN 70121?
SessionSetup           allocate a session id
ServiceDiscovery       what does the station offer
PaymentServiceSelection EIM (card) or Contract (Plug & Charge)
Authorization          may loop while the user authenticates
ChargeParameterDiscovery ⭐ exchange battery and station power limits
CableCheck             DC insulation test
PreCharge              match output voltage to battery voltage
PowerDelivery(Start)   close the contactors
CurrentDemand ×N       ⭐ the ~250 ms control loop
PowerDelivery(Stop)
WeldingDetection       is a contactor stuck closed?
SessionStop
```

---

## OCPP internals / OCPP 内部

| Term | Meaning | 中文 |
|---|---|---|
| **OCPP-J** | The JSON-over-WebSocket binding (as opposed to the obsolete SOAP one) | JSON over WebSocket 绑定 |
| **CALL / CALLRESULT / CALLERROR** | The only three frame shapes: `[2,uid,action,payload]`, `[3,uid,payload]`, `[4,uid,code,desc,{}]` | 仅有的三种帧形 |
| **idTag** | The credential presented to start a transaction (RFID card, app token) | 启动事务时出示的凭证 |
| **Transaction** | One charging session, from `StartTransaction` to `StopTransaction` (OCPP 2.0.1 merges both into `TransactionEvent`) | 一次充电事务 |
| **SetChargingProfile** | ⭐ The backend pushes a power/current schedule the station must obey | ⭐ 后台下发桩必须遵守的功率/电流曲线 |
| **ChargingProfilePurpose** | `ChargePointMaxProfile` (station-wide ceiling) < `TxDefaultProfile` < `TxProfile` (this transaction). More specific wins, but the station max is always a ceiling | 优先级递增；但全桩上限始终是硬上限 |
| **stackLevel** | Within one purpose, the highest wins | 同一 purpose 内数值最大者生效 |
| **Smart charging** | Using profiles to shape demand — the whole point of load management | 用曲线塑形需求，负荷管理的核心 |
| **OCPI** | Open Charge Point Interface — **backend to backend** (roaming between operators). Different layer, do not confuse it with OCPP | **后台之间**的协议(跨运营商漫游)，与 OCPP 不是一层，别混 |

### OCPP version cheat sheet / 版本速查

| Version | Transport | Key change |
|---|---|---|
| 1.6-S | SOAP | Obsolete |
| **1.6-J** | JSON/WebSocket | The largest installed base. Start here |
| **2.0.1** | JSON/WebSocket | `TransactionEvent` replaces Start/Stop (1.6's transaction state was ambiguous); formal device model; ISO 15118 Plug & Charge certificate messages; stronger security profiles |
| **2.1** | JSON/WebSocket | Bidirectional / V2G, DER grid services, battery swapping |

---

## Tooling / 工具

| Term | Meaning |
|---|---|
| **Node-RED** | Flow-based programming on Node.js; wire nodes together in a browser. Used in this domain as test-bench glue, mock backends, and protocol bridges |
| **CSMS simulator** | A fake backend, so you can make it misbehave on purpose |
| **Charge point simulator** | A fake station, so you can run a hundred of them |
