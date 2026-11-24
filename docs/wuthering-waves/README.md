# 鸣潮 PC 资源包

鸣潮资源包维护国服官服、B 服和国际服的下载清单。客户端按固定提交接收数据，默认同步至 `$HOME/.SSMT4-Linux/resource/Game/Wuthering Waves/`。[上游源码与官方样本对照](源码对照.md)

| 路径 | 用途 |
| --- | --- |
| [sources.toml](../../Game/Wuthering%20Waves/sources.toml) | 渠道、来源、默认档位和包入口 |
| [图标](../../Game/Wuthering%20Waves/icon.webp)、[背景](../../Game/Wuthering%20Waves/background.webp) | 配置中的 `entrypoints.icon` 和 `entrypoints.background` 分别登记这两个角色 |
| [manifests/package.json](../../Game/Wuthering%20Waves/manifests/package.json) | 客户端交付白名单，每项登记相对路径、字节数及 SHA-256 |
| [manifests/index.json](../../Game/Wuthering%20Waves/manifests/index.json) | 已提交快照的目录和 SHA-256 |
| `manifests/<渠道>/<发布类型>/<画质>.json` | 可直接读取的完整下载表 |
| `manifests/upstream/<md5>.json` | 按官方 MD5 去重的上游索引原件 |

## 仓库维护命令

在资源仓库根目录运行。读取 B 服 HD 正式清单时可将结果写入文件：

```sh
python3 -B -m Github wuthering-waves fetch --channel bilibili --quality HD -o /tmp/wuwa-bilibili.json
```

维护者可以用 `plan` 和本机状态样例验证安装、更新、转服、卸载的预期动作。空目录安装可省略状态，其他操作读取已装回执和现场文件摘要。这是仓库侧的参考实现。[计划样例和数据约定](执行计划.md)

```sh
python3 -B -m Github wuthering-waves plan install --channel cn --quality HD -o /tmp/wuwa-install.json
python3 -B -m Github wuthering-waves plan switch --state local-state.json --channel bilibili -o /tmp/wuwa-switch.json
```

同服更新使用 `plan update --state local-state.json`，卸载使用 `plan uninstall --state local-state.json`。鸣潮适配器生成清单、补丁和风控动作；实际执行及状态登记属于客户端核心。生产风控列表目前为空。

生成候选时，脚本复用已发布快照中通过校验的对象。所有渠道校验成功后，统一入口只同步变动的文件。

```sh
python3 -B -m Github update --game wuthering-waves
python3 -B -m unittest discover -s Github -t . -v
```

## 按固定提交读取

启动器先确定独立资源仓库的提交 SHA，再取该提交下的 `Game/Wuthering Waves/manifests/package.json`，按登记的路径和摘要接收数据。`sources.toml` 的 `type = "game"`、稳定 `id`、名称、图标、背景和目录入口用于游戏注册。B 服 HD 的目标表由 `manifests/index.json` 中的 `channels.bilibili.formal.qualities.HD` 给出。其 `path` 相对于 `manifests/`，补丁的 `md5` 指向同一提交的 `upstream/<md5>.json`。

自动更新及接入其他游戏的方式见[仓库说明](../../README.md#游戏清单更新)。
