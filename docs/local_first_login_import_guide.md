# 本地首登 + 导入服务器

## 目标

新增用户时，不再让阿里云服务器直接走 Garmin SSO 登录。
改为在本地电脑完成首登，拿到 OAuth token 后上传到服务器，再由服务器完成首次接入。

## 本地执行脚本

本机临时脚本路径：`/tmp/garmin_local_first_login_import.py`

查看帮助：

```bash
python3 /tmp/garmin_local_first_login_import.py --help
```

## self 模式示例

```bash
python3 /tmp/garmin_local_first_login_import.py \
  --name 张三 \
  --email zhangsan@example.com \
  --password 你的 Garmin 密码 \
  --push-mode self \
  --pushplus-token 用户自己的 PushPlus Token
```

## friend 模式示例

```bash
python3 /tmp/garmin_local_first_login_import.py \
  --name 张三 \
  --email zhangsan@example.com \
  --password 你的 Garmin 密码 \
  --push-mode friend \
  --friend-token 好友令牌
```

## 脚本做的事

1. 在本地电脑调用 `garminconnect` 完成 Garmin 登录。
2. 本地生成 OAuth token 文件。
3. 通过 `scp` 把 token 文件上传到服务器 `/root/garmin_assistant/tokens/<邮箱目录>/`。
4. 远程调用 `/root/garmin_assistant/scripts/add_user_and_onboard.py` 登记用户并触发首次接入。

## 为什么这样更稳

- 本地电脑首登走的是你本机网络出口，不再依赖阿里云服务器的 SSO 出口 IP。
- 服务器只复用已经拿到的 token，不需要再发起 Garmin 首登。
