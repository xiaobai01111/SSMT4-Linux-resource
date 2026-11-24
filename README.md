# 资源目录

本目录用于存放 SSMT4-Linux 项目所需的资源文件。

 'DXVK' 该目录存放DXVK家族URL的配置信息


## 游戏清单更新

`Game/<游戏目录>/` 使用统一的 [目录、字段和安装清单协议](docs/game-resource-packs.md)。客户端按固定 Git 提交和 `manifests/package.json` 校验数据，只解析规范安装清单；`Github/` 中的发布工具负责转换各发行商的原始数据，不在客户端执行。

游戏图标和背景分别使用 `icon.webp`、`background.webp`，保留原始尺寸，图标无损编码，背景有损压缩。替换素材时在 `sources.toml` 的 `entrypoints` 登记路径，并更新 `manifests/package.json` 中配置和图片的大小与 SHA-256。

在本仓库根目录使用 Python 3.11 或更新版本：

```sh
python3 -B -m Github update
python3 -B -m Github update --game wuthering-waves
python3 -B -m Github snapshot --game wuthering-waves --output /tmp/wuwa-snapshot
python3 -B -m unittest discover -s Github -t . -v
```

`update` 默认获取全部已登记游戏的远程清单，校验各自的完整候选后与 `Game/<游戏目录>/manifests/` 逐文件比对。仅写入新增或内容改变的文件，移除远程快照不再包含的条目，未变文件不重写。某款游戏拉取或校验失败时保留旧清单、继续处理其他游戏，最后报告失败。`snapshot` 只生成供对比的候选，不修改本地清单。

统一的 [GitHub Actions 工作流](.github/workflows/game-resources.yml) 每天 00:17 UTC（北京时间 08:17）定时运行一次，也可手动触发。仅默认分支可发布，有清单变化才提交；拉取请求只运行离线测试。

接入新游戏时先按协议发布数据；静态包无需修改客户端，也无须登记更新器。若要自动同步上游，在 `Github/` 加入发布端生成器，并在 [UPDATERS](Github/sync.py) 登记稳定 ID、资源目录名和生成函数。生成函数读取来源配置及旧清单缓存，先生成完整候选，再写交付单。

[鸣潮资源说明](docs/wuthering-waves/README.md)
