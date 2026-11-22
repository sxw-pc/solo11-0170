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
