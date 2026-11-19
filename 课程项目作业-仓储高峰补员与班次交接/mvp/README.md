# MVP：仓储高峰补员与班次交接 · 业务核对助手

课程要求对应的四个工程点：

| 课程要求 | 本目录实现 |
| --- | --- |
| FastAPI | `app/main.py`，提供工单、知识库、图谱、审计、Harness 接口 |
| Harness | `app/harness.py` + `tests/`，27 个 pytest 用例与 1 套评估指标 |
| 知识库 RAG、LLM Wiki（知识图谱） | `app/kb.py`（BM25 + 抽取式回答 + 拒答）、`app/graph.py`（图谱与 Wiki 词条） |
| 权限：管理员（SaaS）、用户（分层） | `app/auth.py`，6 类角色 + 租户隔离 + 职责分离 + 哈希链审计 |

## 快速开始

```bash
cd mvp
python3 -m pip install -r requirements.txt
python3 -m uvicorn app.main:app --reload --port 8000
```

浏览器访问 http://127.0.0.1:8000 。

演示账号：

| 账号 | 密码 | 角色 | 说明 |
| --- | --- | --- | --- |
| admin | admin123 | 管理员（SaaS） | 跨租户、用户管理、重置演示数据 |
| gu.minglan | demo123 | 业务管理员 | 本租户全部工单、知识库与图谱维护 |
| lin.yizhou | demo123 | 排期协调员 | 创建、提交、交接 |
| tang.yingqiu | demo123 | 排期协调员 | 同上 |
| fu.jing | demo123 | 质量复核 | 审批、审计查看 |
| qin.rui | demo123 | 一线经办人 | 仅本人范围工单 |
| ye.shu | demo123 | 只读用户 | 只读查看 |

## 运行测试与 Harness

```bash
cd mvp
python3 -m pytest -q
python3 -m app.harness
```

当前结果：`27 passed`；Harness 31 项检查全部通过，检索首位命中率 0.9、引用覆盖率 1.0、拒答准确率 1.0、抽取式回答率 1.0。

## 核心业务规则

1. **缺口三分法**：覆盖缺口 = 需求 − 可立即上岗；培训缺口 = min(可培训人数, 覆盖缺口)；技能缺口 = 覆盖缺口 − 培训缺口。三者不得合并。
2. **证书拦截**：叉车证过期或无证人员标记为“不可上岗（证书问题）”，不得进入可立即上岗名单。
3. **交接四类必填**：未完成事项（含库位/单据号）、设备与工具、异常与风险、确认人；缺一项不能完成责任转移。
4. **职责分离**：提交人不能审批自己的工单；审批必须由质量复核或业务管理员完成。
5. **审计五要素**：操作者、时间、动作、对象、前后值/理由，使用 SHA-256 哈希链串联。
6. **知识库不编造**：只引用已入库段落；检索覆盖不足时拒答，并列出需要补充的资料。

## 目录

```
mvp/
├── app/
│   ├── main.py        FastAPI 路由与接口
│   ├── auth.py        认证、RBAC、会话
│   ├── domain.py      缺口分解、人员匹配、交接校验
│   ├── kb.py          中文 BM25 检索、抽取式回答、拒答
│   ├── graph.py       知识图谱与 Wiki 词条
│   ├── harness.py     自动检查与指标
│   └── store.py       JSON 持久化、哈希链审计
├── data/
│   ├── kb/            5 份知识库文档（规则、口径、交接、权限、背景）
│   ├── graph.json     25 个节点、30 条关系的业务图谱
│   ├── roster.json    14 名合成候选人
│   ├── tickets.json   种子工单（38 人高峰补员）
│   └── users.json     演示账号（密码已哈希）
├── static/            单页前端（原生 JS，无构建步骤）
├── tests/             27 个接口与业务用例 + 评估集
└── requirements.txt
```

## 主要接口

| 方法 | 路径 | 权限 |
| --- | --- | --- |
| POST | `/api/auth/login` | 公开 |
| GET | `/api/me` | 登录 |
| GET | `/api/tickets` | ticket:read |
| POST | `/api/tickets` | ticket:create |
| POST | `/api/tickets/{id}/submit` | ticket:submit（创建人） |
| POST | `/api/tickets/{id}/review` | ticket:review（非创建人） |
| POST | `/api/tickets/{id}/handover` | ticket:handover（交接完整） |
| GET | `/api/tickets/{id}/candidates` | ticket:read |
| POST | `/api/kb/ask` | kb:read |
| GET | `/api/graph`、`/api/wiki/{id}` | graph:read |
| GET | `/api/audit` | audit:read |
| GET | `/api/harness/run` | 登录 |

完整 OpenAPI 见 http://127.0.0.1:8000/docs 。

## 边界与限制

- 数据全部为合成演练数据，不对应真实员工、客户或合同；
- 知识库为 5 份课程文档，检索为 BM25，不是生产级向量检索；
- 人员匹配只做规则打分，不做自动派岗；
- 身份认证为演示级内存会话，未接入企业 SSO；
- 未接入真实 WMS、候选人库、培训台账；
- 未实现知识库在线编辑、租户自助开通、邮件通知等生产功能。

## AI 辅助说明

本目录代码与文档由 AI 编程助手辅助生成，已在 `../docs/` 中说明辅助范围。使用者应逐项运行测试、理解规则、自行修改后再提交；AI 辅助不代表本人已独立完成或老师已验收。
