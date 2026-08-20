# 14-Day Plan / 两周计划

One rule: **every day ends with something committed.** A day that produces
only reading produces nothing you can show.
一条规则: **每天结束时都要有东西提交。** 只有阅读的一天，等于没有产出。

---

## Week 1 — Build / 第一周 · 造

### Day 1 · Concepts + OCPP hello world / 概念 + OCPP 入门
- [ ] Read [`GLOSSARY.md`](GLOSSARY.md) top to bottom. Then close it and write
      the ISO 15118 stack from memory, bottom to top. Check yourself.
      读完术语表，合上，默写 ISO 15118 协议栈，然后对答案。
- [ ] `pip install ocpp websockets`, run the upstream `v16` example from
      [mobilityhouse/ocpp](https://github.com/mobilityhouse/ocpp).
- [ ] Answer in one sentence each: Why PLC and not WiFi? Why EXI and not XML?
      What is DIN 70121 and why does it still matter?
      各用一句话回答: 为什么用 PLC 不用 WiFi? 为什么用 EXI 不用 XML?
      DIN 70121 是什么，为什么它到今天还重要?
- **Commit:** notes in `docs/`.

### Day 2 · Project 01 end to end / 项目 01 跑通
- [ ] Run `csms.central_system` + `cp.charge_point`, read every raw JSON frame.
- [ ] Drive it with `--remote-start-after` instead of `--autostart`.
- [ ] Break `Authorize` on purpose (`idTag = BADCAFE`) and trace the abort.
- **Commit:** your own session log.

### Day 3 · The pure logic / 纯逻辑
- [ ] `cp/profile.py` TODO 1 — `Recurring` profiles (daily night tariff).
- [ ] TODO 2 — W ↔ A conversion. Write down your assumed nominal voltage.
- [ ] TODO 3 — `minChargingRate` → `SuspendedEVSE`.
- [ ] `cp/state_machine.py` TODO 1 — error-code latch.
- **Commit:** tests green, and they were red first.

### Day 4 · Make it look like a product / 让它像产品
- [ ] `docker compose up -d` (SteVe), register `CP_1`, connect to it.
- [ ] `RemoteStartTransaction` from SteVe's web UI.
- [ ] `charge_point.py` TODO 4 — offline queueing. Kill the CSMS mid-transaction.
- **Commit:** a screenshot of SteVe showing your transaction.

### Day 5 · Node-RED flow / Node-RED 流程
- [ ] Import `flows.json`, connect the station, click every inject button.
- [ ] Read `decode OCPP-J frame` line by line.
- **Commit:** a modified flow — add one node of your own.

### Day 6 · Dashboard / 仪表盘
- [ ] Install `node-red-dashboard`, import `flows-dashboard.json`, wire the links.
- [ ] Finish the `ui_button` TODO so the dashboard replaces the inject nodes.
- **Commit:** 📸 `screenshots/dashboard.png`.

### Day 7 · Test bench / 测试台架
- [ ] Alarm path: `Faulted` or `CALLERROR` → red indicator + alarm log.
- [ ] One-button scenario: start → wait → curtail → wait → stop, with assertions.
- [ ] Write project 02's README section on what you added.
- **Commit:** the scenario flow.

---

## Week 2 — Read, break, explain / 第二周 · 读、搞坏、讲清楚

### Day 8 · A V2G session completes / 跑通一次 V2G 会话
- [ ] Clone [EcoG-io/iso15118](https://github.com/EcoG-io/iso15118), create the
      `veth` pair, reach `SessionStop` with no errors.
- [ ] Note which protocol was negotiated — ISO 15118-2 or DIN 70121?
- **Commit:** `captures/session-01.log`.

### Day 9 · See the bytes / 看见字节
- [ ] `tcpdump` + Wireshark. Identify SDP (UDP) → TCP → V2GTP → EXI.
- [ ] Build [OpenV2Gx](https://github.com/uhi22/OpenV2Gx), decode one EXI
      message by hand. Do it once; it demystifies everything.
- **Commit:** the decoded message, with your annotations.

### Day 10 · Break it / 搞坏它
- [ ] Inject one fault (impossible power range / CableCheck timeout / wrong
      ResponseCode).
- [ ] Trace the root cause **by hand first**, before any tool.
- [ ] Run project 04 on your own logs from days 2 and 8.
- **Commit:** `captures/session-02-fault.log`.

### Day 11 · The V2G parser / V2G 解析器
- [ ] Fix the regex in `04-v2g-log-analyzer/v2ganalyzer/parsers/v2g_text.py`
      to match what your stack actually prints.
- [ ] Extract `ResponseCode`; anything not `OK_*` is a finding.
- [ ] Implement R007 (out-of-order) and R008 (truncated session).
- **Commit:** tests for both new rules.

### Day 12 · The report / 报告
- [ ] Fill in `03-iso15118-analysis/report/REPORT-TEMPLATE.md`.
- [ ] The current plot: `EVTargetCurrent` vs `EVSEMaximumCurrentLimit` over the
      whole session, with every gap explained.
- [ ] `render.py` TODO 2 — `--format html`.
- **Commit:** 📄 the finished report.

### Day 13 · Polish / 打磨
- [ ] Every README: does the quick start work on a clean checkout? Try it.
      在干净 checkout 上试一遍每个 README 的快速开始。
- [ ] Screenshots into the top-level README.
- [ ] Remove any TODO you actually finished; keep the ones you did not, and be
      honest about it. An honest backlog reads better than a fake-complete repo.
      删掉真做完的 TODO，留下没做的并如实标注。诚实的待办列表比假装完成好看得多。

### Day 14 · Rehearse / 排练
- [ ] Read your interview Q&A notes, answer every question **out
      loud**, twice. Silently is not the same thing.
      **出声**回答每个问题，两遍。默读不算。
- [ ] Time yourself explaining each project in 90 seconds.
- [ ] Prepare three questions of your own to ask them.
      准备三个你要反问对方的问题。

---

## If you only have one week / 如果只有一周

Drop project 03's fault injection and project 04's V2G parser. Keep:
砍掉项目 03 的故障注入和项目 04 的 V2G 解析器。保留:

| Day | Do |
|---|---|
| 1–2 | Project 01 running, `SetChargingProfile` honoured |
| 3–4 | Project 02 dashboard + 📸 screenshot |
| 5 | Project 03 — one clean session, note which protocol was negotiated |
| 6 | Project 04 on your own OCPP logs |
| 7 | READMEs, screenshots, rehearse |

The screenshot and the analyzer output are the two artefacts that survive
being scaled down. Protect them.
截图和分析器输出是砍需求后仍然存活的两个产物，优先保住它们。
