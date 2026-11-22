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

## 票务清算（跨令牌与隐私受限身份凭据）

闸机卡每天轮换匿名令牌，乘客可能在换乘途中换令牌。`fare_reconcile` 只在发行方存入一次性盲化轮换见证（`rotation_witness`）时才把多个令牌视为同一付费主体：系统仅得到等价类与证明摘要，得不到真实姓名；同站同时间的地理接近永远不能替代该证明。纯匿名按次产品（`per-ride`）明确禁止任何跨令牌合并，借用实名产品的盲链接会被拒绝。

- 实名封顶产品：盲证明验明后按等价类合计扣款并套用 `daily-cap` 封顶，形成可退余额；证明迟到前只保留可解释的封顶上下界（`bound_lower`/`bound_upper`），不向无权账户抢先退款。
- 纯匿名按次产品：每笔原扣款各自保留，单边进出分别挂 `open_in_review`/`open_out_review` 待核与异常闸机扣款，不推断封顶、不追溯乘客。
- 候选行程按业务时间贪心配对进/出站，每个 tap 只能闭合一个账户的一次行程；卡片防重放计数器（`tap_counter`）拒绝重放值，离线闸机补传（`gate_tap.offline`）产生复核待办。
- 扣款与退款均为只追加事件：退款（`fare_refund`）永不覆盖原扣款；见证迟到撤销时退款收据保留，超出单令牌边界的金额转为 `backcharge` 待办。跨 UTC 日的扣款按日分别封顶，跨日配对行程挂 `cross_day_review`。
- `fare-reconcile` 子命令只导出等价类、证明哈希与清算所需摘要，盲化链接材料单独保管，无法从导出表逆推姓名。

```powershell
python -m liftassist fare-import --db station.sqlite --file fixtures/fare-reconcile-common.json
python -m liftassist fare-import --db station.sqlite --file fixtures/fare-reconcile-world-a.json
python -m liftassist fare-reconcile --db station.sqlite
```

`fare-reconcile-world-a`（实名封顶＋盲链接）与 `fare-reconcile-world-b`（纯匿名按次、无链接）的闸机记录、扣款与令牌有效期完全相同，仅发行方受控链接与产品生效历史不同：前者合计 600 分、封顶 500、应退 100；后者两个 300 分原扣各自保留且双边单边行程待核，不产生任何退款。票务清算不决定电梯接驳或旅客服务承诺。
