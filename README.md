# NewEra Command Deck MVP

基于 PRD 的首个可运行切片：后端优先落地 OBJ-01 + OBJ-02（对象模型 + 状态机），前端按 UI-01 到 UI-05 建立 Apple 风调度台、执行链、执行舱和决策抽屉。

## 快速开始

### 后端

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --reload --port 8000
```

### 前端

```bash
cd frontend
npm install
npm run dev
```

默认前端请求 `http://localhost:8000`，也可以通过 `VITE_API_BASE` 覆盖。

## 已落地范围

- OBJ-01：Workitem / Executor / Workflow / Trace / Artifact / AcceptanceCriterion / Budget 领域模型。
- OBJ-02：Workitem 状态机、转移规则、Trace 副作用、决策/验收/升级校验。
- API-01：Workflow / Workitem 动作接口与 Trace 查询。
- API-02：Executor 列表与推荐接口。
- UI-01：设计 token、玻璃拟态卡片、状态色、优先级色。
- UI-02 至 UI-05：调度台、执行链、执行舱、决策抽屉 MVP。

## 验证

```bash
python3 -m unittest discover backend/tests
python3 -m py_compile backend/app/domain/*.py backend/app/*.py
```
