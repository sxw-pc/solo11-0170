# 地铁站电梯接驳履约采证台

离线模拟站内无障碍旅客服务。电梯故障与复测、旅客行程承诺、人员资格/排班与人工接驳回执各有独立入口和持久化账本。电梯停运会使已承诺路线转为待重排，旅客仍有接驳服务债务；工单关闭但缺少独立载荷复测不能恢复开放。人员证书或班次不覆盖服务窗口也不能承诺路线。旅客或独立见证缺失时，人员单方签到不能宣称送达。

```powershell
python -m pip install . --no-deps --no-build-isolation
python -m liftassist demo --db demo.sqlite
python -m liftassist audit --db demo.sqlite
python -m unittest discover -s tests -v
```

单独测试三个业务入口：

```powershell
python -m liftassist lift-import --db station.sqlite --file fixtures/lift-ok.json
python -m liftassist assist-import --db station.sqlite --file fixtures/staff-ok.json
python -m liftassist assist-import --db station.sqlite --file fixtures/terms-ok.json
python -m liftassist journey-import --db station.sqlite --file fixtures/journey-ok.json
python -m liftassist lift-import --db station.sqlite --file fixtures/lift-bad.json
python -m liftassist assist-import --db station.sqlite --file fixtures/assist-ok.json
python -m liftassist report --db station.sqlite
```

`lift-bad` 把有效路线转待重排；`journey-bad` 因轮椅尺寸不可达；`assist-bad` 的单方签到不算履约。十种主要决定分别为故障封锁、独立复测恢复、故障身份冲突、轮椅通道适配、换乘时限、人员时段互斥、停运后承诺重排、独立到场见证、迟到补偿、已服务后的历史承诺保留。另有资质撤销、班次外服务争议和独立赔付合同：迟到分钟数按生效合同的每五分钟费率计算并受上限约束，缺合同保留待核义务而不编造金额。每个源文件保存原字节与 SHA-256，同事件 ID 异内容拒绝，同文件重放幂等；一份文件内任何事件失败则整笔事务回滚。`audit` 从事件账本重算各业务表并比较当前投影。所有人物、站点与时间均为合成 fixture；没有接入真实门禁、人员身份认证或列车实时调度。

业务链一：故障/检修复测改变电梯可用性，进而改变旅客承诺并创建替代服务债务。行程可声明多个连续节点与电梯段；任一段失效都使整条路线待重排。业务链二：旅客需求、换乘时限和人员班次决定接驳是否有资格兑现；迟到送达按冻结的赔付合同形成金额。业务链三：已确认送达不因随后电梯停运而倒改为未服务，未执行的替代接驳则可在复测后取消。上述结果均可从留存的原始事件重新计算。

业务链四（轮椅实物与标签分离身份账）：RFID 标签可被拆下、补贴或被维修商重复写入同一 EPC，闸门读到标签不等于轮椅实物回到库位。`asset-custody-import` 接收车架刻号登记、读头位置、标签写入/拆装证书（形成标签安装世代）、独立车架见证、库位重量/轮廓观测、封存盘点令、盘点员核验、跨站调拨授权、承运接收与追索。标签号、资产号、保管人三种身份分别记账：扫码与人工回执只是"声称"，车架刻号图像、库位重量/轮廓与承运封签接收才是独立实物证据。没有独立实物证据的库存记为待实物核验，不计入可借库存，`mobility_reserve` 对非 `verified`/`untracked` 状态的资产拒绝借出。同一 EPC 并存于两台车、同一车架短时两处被见证、实物证据与最新声称地点矛盾时生成可定位的冲突集合。两份已批准物权方案可在同一待核原件上启动：站内封存盘点（暂停借出、独立盘点员查刻号，查无实物升级失踪调查）与跨站维修调拨（所有人授权在途、到站期限、过期未接收向承运方追索）。迟到拆标证书与接收见证只重算现势库存与追索，不倒写已录入的回执与扫描；已完成的旅客借用保持原履约事实，也不重新分配接驳班次。`custody-report` 输出每台轮椅现势保管人/位置/实物状态、标签与实物分离视图、各库位可用实物数与未决冲突。

```powershell
python -m liftassist mobility-import --db station.sqlite --file fixtures/asset-custody-asset.json
python -m liftassist asset-custody-import --db station.sqlite --file fixtures/asset-custody-world-a.json
python -m liftassist custody-report --db station.sqlite
```
