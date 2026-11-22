# 地铁站电梯接驳履约采证台

离线模拟站内无障碍旅客服务。电梯故障与复测、旅客行程承诺、人员资格/排班与人工接驳回执各有独立入口和持久化账本。电梯停运会使已承诺路线转为待重排，旅客仍有接驳服务债务；工单关闭但缺少独立载荷复测不能恢复开放。人员证书或班次不覆盖服务窗口也不能承诺路线。旅客或独立见证缺失时，人员单方签到不能宣称送达。消防演练另设烟气—人流耦合疏散账本：防火门开闭同时改变可走边与压差烟气边界，安全容量随已发指令演化，静态最大流不能作为紧急疏散的安全证明。

```powershell
python -m pip install . --no-deps --no-build-isolation
python -m liftassist demo --db demo.sqlite
python -m liftassist audit --db demo.sqlite
python -m unittest discover -s tests -v
```

单独测试四个业务入口：

```powershell
python -m liftassist lift-import --db station.sqlite --file fixtures/lift-ok.json
python -m liftassist assist-import --db station.sqlite --file fixtures/staff-ok.json
python -m liftassist assist-import --db station.sqlite --file fixtures/terms-ok.json
python -m liftassist journey-import --db station.sqlite --file fixtures/journey-ok.json
python -m liftassist lift-import --db station.sqlite --file fixtures/lift-bad.json
python -m liftassist assist-import --db station.sqlite --file fixtures/assist-ok.json
python -m liftassist evacuation-import --db station.sqlite --file fixtures/evac-scene-ok.json
python -m liftassist evac-plan-import --db station.sqlite --file fixtures/evac-plan-ok.json
python -m liftassist report --db station.sqlite
```

`lift-bad` 把有效路线转待重排；`journey-bad` 因轮椅尺寸不可达；`assist-bad` 的单方签到不算履约。十种主要决定分别为故障封锁、独立复测恢复、故障身份冲突、轮椅通道适配、换乘时限、人员时段互斥、停运后承诺重排、独立到场见证、迟到补偿、已服务后的历史承诺保留。另有资质撤销、班次外服务争议和独立赔付合同：迟到分钟数按生效合同的每五分钟费率计算并受上限约束，缺合同保留待核义务而不编造金额。每个源文件保存原字节与 SHA-256，同事件 ID 异内容拒绝，同文件重放幂等；一份文件内任何事件失败则整笔事务回滚。`audit` 从事件账本重算各业务表并比较当前投影。所有人物、站点与时间均为合成 fixture；没有接入真实门禁、人员身份认证或列车实时调度。

业务链一：故障/检修复测改变电梯可用性，进而改变旅客承诺并创建替代服务债务。行程可声明多个连续节点与电梯段；任一段失效都使整条路线待重排。业务链二：旅客需求、换乘时限和人员班次决定接驳是否有资格兑现；迟到送达按冻结的赔付合同形成金额。业务链三：已确认送达不因随后电梯停运而倒改为未服务，未执行的替代接驳则可在复测后取消。上述结果均可从留存的原始事件重新计算。

业务链四（紧急疏散，不重新承诺普通旅客换乘行程）：`evacuation` 账本只采证边/区域/烟感/防火门，事件决定一律为 `captured`，不生成疏散时限。`evac_plan` 在带时间的有向网络上按分钟推演：开门指令同时开放通行边与压差驱动的烟气传播路径，关门则阻断烟气；批次只有在“当时可知证据”能证明整段行走时间安全时才放行，入口烟度本身从不构成放行依据。传感器迟滞与时钟漂移形成悲观/乐观烟气到达界，未回执的门禁指令不改变确认状态，重复人流计数只保留首条证据，运行中的排烟风机可清除节点烟度。事前批准的两种处置为密闭暂避（FD1 关闭、同层 20 分钟防烟避难区、消防救援交接与避难计时）和西侧楼梯转移（独立无烟 E2，按 10 人/分钟分批，首末批时刻不同并安排楼梯口引导与无障碍转运）。静态最大流会宣称 E1 四分钟清空 80 人，耦合推演则在第三分钟即记录至少 40 人的烟气暴露；迟到烟感只追加暴露行并标注原计划实际暴露区间，已公告指令、已通过人员与已进入烟区的人永不倒改。每个预案留存逐分钟滞留轨迹、可安全离开人数、需救援滞留人数、消防交接任务与门禁指令，全部投影可经 `audit` 从事件账本重放核对。
