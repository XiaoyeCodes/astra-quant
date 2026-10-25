# ASTRA 接手记录（2026-10-25）

## 工作目录与分支

- 当前运行项目：`C:\Users\ATLAS\astra-quant`。
- 仓库：`XiaoyeCodes/astra-quant`；沿用 `fix/octobot-2.1.1-paper-e2e` 分支和 PR #1。
- `C:\Users\ATLAS\Documents\ChatGPT\Bitget量化` 是此前导入压缩包的另一个 checkout，本次没有修改它。

## Claude 已完成的部分

- OctoBot 2.1.1 接入修复和 Bitget 现货模拟盘端到端测试。
- 13 个预设、手动/AI 交易/AI 选策略/指标选策略四种方案，以及行情雷达。
- 主止损和 OctoBot 备用止损，均在模拟盘验证过；权益、重连及旧成交识别修复。
- 具体历史结果保留在 `docs/TEST_REPORT.md`。

## 本次完成

- 将用户指定的本机 DeepSeek 配置接入 `.env`，认证成功，使用 `deepseek-flash`。只使用 DeepSeek Key，没有将配置文件中的其他服务凭据写入项目。
- 修复短 JSON 请求的思考 token 预算问题、旧 AI 信号字段名、自动切换连续确认，以及不可信 AI 回答的校验。
- 实测 AI 雷达、OctoBot AI 信号、两次真实 AI 判断后的模拟盘自动切换；13 个无网络回归测试通过。
- 更新了测试报告，并清理测试期间 OctoBot 自动扩写的受版本控制模板。

## 交付时运行状态

- 驾驶舱：`http://localhost:8080`；OctoBot：`http://localhost:5001`。
- Bitget 公开接口，模拟盘；没有交易所交易密钥。
- Profile：`astra_idle`；方案：`manual`；最大止损 10%；原始起点 2205.56 USDT。
- DeepSeek Key 保存在 Git 忽略的 `.env`，不应输出、提交或写入文档。
- 测试的原始状态备份和无密钥 API 观察记录位于本机 `data/handoff-20261025-134033/`。

## 后续事项与边界

1. **AI 仓位**：`astra_ai_index` 的多智能体、新闻及情绪源链路尚未端到端验证。AI 信号通过不等于这个策略已通过。
2. **分批补仓止盈/止损**：真实行情模拟盘测试期间没有走完首单成交与退出链路。需要受控行情回放或继续观察模拟盘，不能将“已经挂单”算作退出已验证。
3. **指数持仓调仓**：此前 6 分钟测试无调仓，仍需从生产者事件、组合估值准备状态及权重权限定位；不要直接归因于一天调仓周期，也不要默认自定义权重一定可用。
4. **休眠恢复**：没有添加后台重启任务。持续无法连接时，现有手动恢复命令是 `docker compose restart octobot`；启动时可能重新检查/安装插件，短暂 unhealthy 不等于已经卡死。
5. **策略参数**：网格、补仓比例、持仓上限及开新轮周期等仍沿用原参数；待用户决定的选项在测试报告第六节。本次修正 AI 置信度字段名是让原有 60% 设置生效。
6. **实盘**：桥接服务的策略切换、自动选策略和止损仍限模拟盘。没有启用实盘或更改相关限制。

## 回归验证

```powershell
python -m unittest discover -s tests -v
docker compose build bridge
docker compose up -d --no-deps bridge
```

服务重建后，用 `/api/state` 核对 `mode=paper`、`profile.id=astra_idle`、`strategy.scheme=manual`、`strategy.stopLoss.manual=10`，并确认没有挂单。不要用会展开环境变量值的 `docker compose config` 输出检查密钥。
