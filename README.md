# 地铁站电梯接驳履约采证台

离线模拟站内无障碍旅客服务。电梯故障与复测、旅客行程承诺、人员资格/排班与人工接驳回执各有独立入口和持久化账本。电梯停运会使已承诺路线转为待重排，旅客仍有接驳服务债务；工单关闭但缺少独立载荷复测不能恢复开放。人员证书或班次不覆盖服务窗口也不能承诺路线。旅客或独立见证缺失时，人员单方签到不能宣称送达。

```powershell
python -m pip install . --no-deps --no-build-isolation
python -m liftassist demo --db demo.sqlite
python -m liftassist audit --db demo.sqlite
python -m liftassist fit-import --db demo.sqlite --file fixtures/boarding-fit-ok.json
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

业务链四（`liftassist/boarding_fit.py`）：站台—车门—桥板全停位适配证。一张停稳照片只是一个停位的 `dock_measurement`，不能证明整段停车包络。测绘折线（含水平/垂直精度、版本与横摆余量）描述弯道边缘与内部凹口，测点插值给出横向缝隙与高差（只内插、不外延），桥板标定给出端部承压区与挠度，车门载荷剖面给出地板高度变化。证书在 1cm 采样网格上叠加所有折线节点与测点，对每个停位保守校验四角/板面足迹越边、有效承压、纵坡（计入载荷与挠度）、轮椅净宽与门宽，输出实际安全停位子区间、所需停车精度和卡住全范围适配的最小几何反例。数据缺口（测绘覆盖不到、测点不包络、零偏移测点缺失、测绘与测量超精度冲突、证据被新版本取代）一律给 `retest_pending`，不沿测点猜安全。两种获批作业方式产生不同结果：受控窗口模式须有精确停车标志或独立停位复核，发仅限该窗口/车门的受限硬件证并生成停位控制任务；保持 ±35cm 普通范围时，短桥板全范围被拒，系统改用同站经过全范围验证的长桥板生成替换硬件任务，无合格长板则生成封门标记与采购任务。迟到测绘版本或挠度复标自动撤回依赖旧证据的有效证书并关闭运营任务，但证书行、反例与决定账本全部保留。该账本只回答某型列车在指定站台/车门/停车精度下哪块硬件可用，不读写任何旅客 `committed` 行程或人员班次。
