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

## 站内连续可见导向链核证

导向设施证书独立于旅客接驳账本：`journeys` 的 `committed` 只读电梯宽度、开门与人员班次，不读 passage/guide_sign/obstacle，因此行程可承诺不等于视觉导向合格。仅在路口量“到最近标牌距离 <10m”会把中途无视线的通道误判合格；核证改在整条可行走带上按轮椅视高区间密集采样（中心线桩距 × 横向 5 个视点），每个视点必须同时满足：下一指示牌在身后保留距之外、文字可读距离（字高×200）内、牌面法线与视线夹角 ≤60°、且三维视线不被有效期内的遮挡物多边形切断。断链以中心线桩号区间输出，并给出最小移牌/补牌坐标；同一牌服务两条支路时附 `revalidate_chains`，移牌后两条链都要重验。

```powershell
python -m liftassist wayfinding-import --db station.sqlite --file fixtures/wayfinding-ok.json
python -m liftassist guide-import --db station.sqlite --file fixtures/guide-static-ok.json
python -m liftassist guide-import --db station.sqlite --file fixtures/guide-cert-static-ok.json
python -m liftassist guide-import --db station.sqlite --file fixtures/guide-assist-ok.json
python -m unittest tests.test_guide_visibility -v
```

两类经站务批准的实施方案严格分证：`static_visual` 方案须把 SI1 移到临时广告板之前、补齐连续视觉接力并重新验收，未完成前该路只能得到 `refused/visual_chain_broken`；`tactile_voice` 方案凭经核验的定位信标与连续盲道覆盖断链，可取得 `issued/non_visual_assist`，其证书 `visual_claimed=0`，报告不得冒写 SI1 全程可见——语音方案事件若声明视觉结论直接拒绝。广告板撤离后静态方案可凭新评估再申请全程可见证，旧辅助证历史保留。证书绑定图件版本（`map_version` 须等于实测 `survey_at`）与评估时刻；迟到的遮挡物原件可追加 `revoked/late_obstacle_record` 修订撤证，但绝不倒改已完成检查的旧修订行与采样明细。未知标牌/遮挡高度、图层坐标偏移、遮挡有效期重叠、照明窗口不覆盖或信标未核验时一律出 `pending`，不默认无遮挡。

