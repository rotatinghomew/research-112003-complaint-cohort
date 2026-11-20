# 投诉关联证据与联合复核

各表为本地合成业务记录，身份与机构代号不对应真实对象。

## 资料格式

资料为 UTF-8 编码、首行为列名的 CSV。空字段表示没有记录，不表示零。连续日号采用同一份内部日历。

| 文件 | 列 | 登记口径 |
| --- | --- | --- |
| `purchases.csv` | `order`、`household`、`campaign_hint`、`course`、`bought`、`jurisdiction` | 购买记录；household 为线索中的账号代号，campaign_hint 不是确认归属。 |
| `claims.csv` | `claim`、`revision`、`received`、`order`、`account`、`severity`、`state` | 同一 claim 的 revision 随时间递增，严重程度由受理人员给出。 |
| `identity.csv` | `id`、`first`、`second`、`received`、`verdict` | 家庭关系凭据；candidate 不能作为确认。 |
| `links.csv` | `id`、`subject`、`course`、`activity`、`start`、`end`、`received`、`verdict`、`ref` | 活动归属；相同效力的确认冲突需待查，ref 指被撤回或排除的凭据。 |
| `shifts.csv` | `shift`、`day`、`jurisdiction`、`slots`、`min_households`、`min_high` | 班次、管辖及每项联合复核的最低覆盖。 |
| `commitments.csv` | `receipt`、`shift`、`activity`、`household`、`claim`、`agreed` | 已认领的材料；同 receipt 代表同一席位，同一活动可另有新的待办范围。 |
| `exposures.csv` | `receipt`、`activity`、`household`、`channel`、`age_day`、`observed` | 同一户可在多个活动购买；age_day 指开场后的日数。历史 old 开头的活动均为不同活动，observed 是资料报入日。 |
| `incidents.csv` | `receipt`、`report_id`、`known`、`independent_review` | 成熟历史的投诉事实通过 receipt 追到购买家庭；independent_review 为复核后严重性，不是历史报警。 |
| `coverage.csv` | `activity`、`channel`、`completed_age`、`known` | 已报齐的活动日龄，其他未齐。历史和当前日号共用同一原点，历史会有负日号，活动日龄始终为正。 |
| `signals.csv` | `activity`、`compare_at`、`age_day`、`followup`、`reference_before`、`min_families`、`scope` | 实际请求的比较截止、跟踪期与最早可发的最小家庭数；切点不使用此后才到达的记录。 |

## 检查资料

使用 Python 3.11 和标准库。在项目根目录运行：

```sh
python3 -m unittest discover -s tests -v
```

`tests/test_sources.py` 只核对资料格式与引用；`tests/test_review.py` 核对下列
规则在这批资料上的行为。

## 复盘工具

`review/` 是按“当日已知材料”重放的分析库，不写回资料，也不替值班组下结论。

```sh
python3 -m review                        # 按资料最新时点输出完整复盘
python3 -m review --as-of 34             # 按指定时点重放陈述、信号与席位
python3 -m review --audit b --as-of 34   # 组长视角：分母、关联、席位、已送材料
python3 -m review --audit b --as-of 34 --json
```

| 模块 | 作用 |
| --- | --- |
| `review/model.py` | 某一时点的家庭合并、投诉当前修订、活动归属有效性；生成陈述。 |
| `review/signals.py` | 按渠道和日龄比较独立家庭投诉率；计算追踪期复核。 |
| `review/seats.py` | 把可处理事项排进班次联合复核席位，列出补证需求和未排原因。 |
| `review/audit.py` | 汇总单个异常的分母完整性、可支持关联、席位占用和已送材料。 |
| `review/report.py` | 文本报告。 |

关键口径：

- **陈述**：只有同一家庭且同一订单的记录合为一份；同一家庭参与不同活动仍是
  不同陈述。家庭合并只认 `confirmed` 的身份凭据，且按凭据到达时点生效；
  `candidate` 合并不了。
- **归属**：活动证据必须同时覆盖课程和购买时间。`confirmed` 才是认定；
  `shared_support`、`shared_payee`、`same_name` 和失效的确认只提示补证。
  被 `withdrawn`/`excluded` 引用后，原凭据不再有效；同一订单出现互相抵触的
  有效确认时状态为 `conflict`，不按到达顺序择一。
- **重放**：所有读取都以 `as_of` 为界，之后到达的修订、凭据、暴露和事件不参与。
  撤回后可以按当时已知材料重看，但不改写当时已送出的判断。
- **信号**：按渠道、活动开场后相同日龄比较；分母是同期在日龄内、且在比较日前
  已报入的合格购买家庭，不用客服流量。`coverage.csv` 未报齐该日龄就不出率。
  基准只取比较日前观察期已满的历史活动，每户只按其最早一次历史暴露计一次，
  参加当前活动的家庭不进入基准。异常判定用单侧二项精确检验（α=0.05）。
- **追踪期**：保留当日判断，只说明追踪期末能否确认。当日无分母或证据不足的
  情况分别标记，不把“活动小、证据不足”说成“确无异常”，也不把补到的归属
  回写成当日判警。
- **席位**：同一 `receipt` 只占一席；已认领家庭不再组成新的席位组合，但可在原
  认领事项内继续办理。先排严重争议；非严重事项只有在该活动当日已有异常证据
  时才占席。归属待核的严重争议只作单件，不扩展活动范围、不合并两个活动。
  独立家庭覆盖不足时留缺口并写明，不用报警条数凑席。
- **已认领材料**：确认证据撤回后原办理归属保留，有争议部分暂停转交，其余仍可
  继续；信号失效或归属更正不替代对已受理严重争议的判断。
