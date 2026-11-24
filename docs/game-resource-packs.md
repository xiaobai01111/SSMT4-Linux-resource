# PC 游戏资源清单包

在独立资源仓库根目录运行 Python 3.11 或更新版本。以下命令从发行商读取正式发布信息和已开放的预下载目标，更新对应 `Game/` 目录中的清单。

```sh
python3 -B -m Github update --game genshin-impact
python3 -B -m Github update --game honkai-star-rail
python3 -B -m Github update --game honkai-impact-3rd
python3 -B -m Github update --game zenless-zone-zero
python3 -B -m Github update --game arknights-endfield
```

`python3 -B -m Github snapshot --game genshin-impact --output /tmp/ssmt4-genshin-snapshot` 生成独立候选。输出路径须尚不存在。`python3 -B -m unittest discover -s Github -t . -v` 运行离线测试。

## 游戏和渠道

| 游戏 | `Game/` 目录 | 渠道 |
| --- | --- | --- |
| 原神 | `Genshin Impact` | 国服官服、国际服 |
| 崩坏：星轨 | `Honkai Star Rail` | 国服官服、国际服 |
| 崩坏3 | `Honkai Impact 3rd` | 国服官服、欧美服、日服、韩服、东南亚服、繁中服 |
| 绝区零 | `Zenless Zone Zero` | 国服官服、国际服 |
| 明日方舟：终末地 | `Arknights Endfield` | 国服官服、B 服、国际服 |

## 数据与交付

`sources.toml` 登记稳定游戏 ID、显示名、默认渠道和发行商入口。`manifests/index.json` 通过相对路径、字节数和 SHA-256 绑定各渠道的 `formal.json` 与可用的 `predownload.json`。

- **HoYoPlay** 使用 `getGameComboInfo` 的正式分支和启动入口，再查询各分支的 `getBuild`。组件保留发行商声明的场景、分块 CDN、压缩方式、实际传输字节数，以及解压后 protobuf 的 MD5。绝区零的 mini 语音可以没有场景声明。`patches` 引自 `getPatchBuild`，每个差分组件只列已开放的源版本。`legacyArchives` 单独记录旧 ZIP 或 7z 的版本、卷表和语音包。正式清单始终使用 Sophon 版本。
- **终末地** 使用发行商 `get_latest` 的分卷 URL、大小和 MD5，按数字卷号排序并检查是否连续。`verification` 保留文件 CDN 根和发行商给出的文件索引摘要。带当前安装版本再次查询可发现预下载差分，`fromVersion` 和 `version` 分别记录源版和目标版。国服和 B 服的临时签名不进入交付单。需要下载时，根据同一渠道和版本重新请求发行商地址，并核对 `locator`、卷名、大小和 MD5。当前国际服接口返回无签名的 `url`。
- **校验范围** 交付包的 SHA-256 覆盖 `sources.toml`、索引和新生成的版本清单。资源 CDN 的 MD5 依上游定义覆盖原始分卷或解压后的 Sophon protobuf。版本清单保存上游 protobuf 的地址、大小与摘要，protobuf 正文在使用时从发布方获取。

## 补充图标和背景

五个包的 `icon` 和 `background` 初始为空。此时交付摘要位于 `manifests/package.pending.json`，现有客户端会跳过这份待交付包。把图片放在游戏目录根，填写 `[entrypoints]` 的相对文件名，再运行对应的 `update --game`。登记图标后生成 `manifests/package.json`，背景可以继续留空。

五个总索引使用 `game-resource-catalog`。SSMT4-Linux 当前的安装入口读取鸣潮正式文件表或 `game-install`。使用这些新包安装游戏需要接入 Sophon 清单与分块重组，或者终末地分卷与官方摘要处理。素材登记完成后客户端可以同步清单数据。
