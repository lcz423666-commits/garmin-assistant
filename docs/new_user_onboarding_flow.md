# 新用户接入流程

## 自有 Token 模式（self）

1. 收集用户姓名、Garmin 邮箱、Garmin 密码、PushPlus Token。
2. 通过 OpenClaw 执行“添加佳明用户”。
3. OpenClaw 调用 `/root/garmin_assistant/scripts/add_user_and_onboard.py` 写入 `config/users.json`。
4. Garmin 助手自动运行首次数据探测、历史回填和初始分析。
5. 后续晨报、运动快报、晚间推送、周报、月报全部使用用户自己的 `pushplus_token`。

## 好友消息模式（friend）

1. 管理员登录 PushPlus 后台，进入好友管理，生成好友二维码。
2. 把二维码发给新用户，让用户用微信扫码成为好友。
3. 在 PushPlus 好友管理页面获取该用户的 `friend_token`。
4. 通过 OpenClaw 执行“添加佳明用户”，并提供：
   - 用户姓名
   - Garmin 邮箱
   - Garmin 密码
   - 推送方式：B / friend
   - 好友令牌 `friend_token`
5. OpenClaw 调用 `/root/garmin_assistant/scripts/add_user_and_onboard.py` 写入 `config/users.json`。
6. Garmin 助手自动运行首次数据探测、历史回填和初始分析。
7. 后续所有推送统一走管理员 PushPlus Token，并在请求里附带该用户的 `friend_token`。

## OpenClaw 补字段规则

- 如果第一次只给了姓名、邮箱、推送方式，脚本会继续追问缺失字段。
- `self` 模式缺少 `pushplus_token` 时，提示补用户自己的 PushPlus Token。
- `friend` 模式缺少 `friend_token` 时，提示补好友令牌。
- 缺 Garmin 密码时，统一提示补 Garmin 密码。

## 配置约定

- `config/system.json` 里的 `pushplus.admin_token` 是管理员 PushPlus Token。
- `config/users.json` 里的每个用户都要有 `push_mode` 字段：
  - `self`：使用自己的 `pushplus_token`
  - `friend`：使用管理员 `admin_token` + 用户的 `friend_token`

## 日志约定

- 所有推送继续走统一的 `push_to_wechat()`。
- Push 日志和运行日志会带上 `push_mode`，用于区分 `self` 与 `friend`。
