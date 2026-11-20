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

## 检查资料

使用 Python 3.11 和标准库。在项目根目录运行：

```sh
python3 -m unittest discover -s tests -v
```

这项检查核对资料格式与引用，不生成业务结论。
