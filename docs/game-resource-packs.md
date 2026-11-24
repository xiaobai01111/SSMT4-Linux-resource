# PC 游戏资源清单包

在独立资源仓库根目录运行 Python 3.11 或更新版本。以下命令从发行商读取正式发布信息和已开放的预下载目标，更新对应 `Game/` 目录中的清单。

```sh
python3 -B -m Github update --game genshin-impact
python3 -B -m Github update --game honkai-star-rail
python3 -B -m Github update --game honkai-impact-3rd
python3 -B -m Github update --game zenless-zone-zero
python3 -B -m Github update --game arknights-endfield
python3 -B -m Github update --game wuthering-waves
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
| 鸣潮 | `Wuthering Waves` | 国服官服、B 服、国际服 |

## `Game/` 目录与配置

| 路径（相对于单个游戏包） | 字段与约束 |
| --- | --- |
| `sources.toml` | `type = "game"`；`id` 是 `[a-z0-9][a-z0-9_-]{0,95}` 的稳定标识；`game` 是非空显示名；`version` 是非负配置版本号，不是游戏发行版本。 |
| `[entrypoints]` | `catalog` 固定写 `manifests/index.json`；`icon` 为相对图片路径，`background` 可省略。推荐根目录 `icon.webp` 和 `background.webp`。 |
| `manifests/package.json` | 交付单：`schema = 1`、`type = "game"`、`id` 和 `files`。每项记录包根相对 `path`、原始字节 `size`、小写十六进制 `sha256`。只交付配置、引用图像、安装索引和安装目标；每文件最多 32 MiB，整包最多 512 MiB。未登记图标时生成不供发现的 `package.pending.json`。 |
| `manifests/index.json` | 唯一的客户端安装索引。 |
| `manifests/releases/**/*.json` | 已固定的安装目标表。 |
| `manifests/publisher.json` 及其引用 | 发行商索引和维护数据，不进客户端交付单，也不参与安装动作分派。 |

游戏包必须是 `Game/` 的直属目录，目录名与稳定 ID 分开。目录名以字母或数字开头，可含 Unicode 字母数字、空格、`.`、`_`、`-`，最多 180 字节；不接受绝对路径、多级路径、尾随空格/点、Windows 设备名、`Prefix` 或符号链接。不要通过改目录名来改显示名称。

`default_channel`、`default_quality` 指向默认选择键，省略等同于空值；非空默认渠道要在 `[channels.<键>]` 声明。`[channels.<键>].name` 和 `[quality_names]` 提供显示标签，[鸣潮配置](../Game/Wuthering%20Waves/sources.toml) 有完整示例。发行商入口字段可放在渠道表供发布端使用，客户端不会据此执行请求或推断可安装版本。

## 通用安装清单

- **[索引示例](../Game/Wuthering%20Waves/manifests/index.json)**：顶层严格使用 `schema: 2`、`type: "game-install"`、`gameId` 和 `releases`。`gameId` 与配置和交付单一致。每条 `releases` 含 `selection`、`path`、`sha256`；同一渠道和画质只能登记一条，目标路径不可重复。`path` 从游戏包根计算，且必须在 `manifests/releases/` 中；摘要必须与 `package.json` 完全一致。`releases: []` 表示只有游戏资料。
- **选择**：`selection` 精确包含 `channel`、`quality`、`version`。渠道和画质键为空字符串时表示不区分，否则以 ASCII 字母或数字开头，允许后续 ASCII 字母数字、下划线、连字符，最多 96 字节。版本是原样展示和核对的非空、无控制字符的字符串，去除首尾空白后不得变化，最多 256 字节，不强制 SemVer。
- **[目标示例](../Game/Wuthering%20Waves/manifests/releases/cn/HD.json)**：顶层严格使用 `schema: 1`、`type: "game-install-target"`、`gameId`、`selection`、`entry`、`assets` 和 `install`。游戏 ID 和整个选择必须与索引一致。`entry` 是最终安装树中的相对入口。资产记录含 `path`、同内容镜像 `urls`、无符号 64 位字节数 `size`、`hash`。`hash` 必须是 `{"sha256":"…"}` 或 `{"md5":"…"}`，优先用 SHA-256；MD5 仅核验发布者给出的整文件，不能授权跨镜像保留部分下载。地址使用无登录凭据的 HTTPS（本机测试可用字面回环 HTTP）。
- **安装动作**：`{"kind":"files"}` 直接部署文件树，入口必须在资产表；`archive` 需要 `format`（`zip`、`7z`、`rar5` 或 `tar`）、按顺序覆盖所有资产的 `parts`、包装根 `root`（`"."` 表示无包装）和准确的总解包字节 `unpackedBytes`；`installer` 需要显式 `setup` 和最终入口的 `entrySha256`，归档也可以同时声明这两个字段。运行向导前须由用户确认。没有隐式执行、通用命令或游戏 ID 特判。

正文路径以 `/` 分隔，保留原始拼写；拒绝父目录、链接、Windows 设备名、大小写/Unicode 折叠后冲突的文件树和不同内容争用同一路径。列表阶段检查索引与交付单，接单冻结时再验证所选目标的确切字节、身份、路径和安装动作。不要把发行商的字段、脚本或预下载记录直接填入安装清单。

## 当前发布范围

- 鸣潮发布三个区服、三档画质的通用直装目标。发布工具读取厂商原始索引并生成可验证的完整文件列表；客户端不解析厂商格式。
- HoYoPlay 保留 Sophon 的正式构建组件、块下载参数、索引摘要、补丁版本及可用的旧压缩卷。Sophon 需要按压缩块重组文件；公开归档的预估解包字段也不保证等于实际字节数。原神、星轨、崩坏3、绝区零目前只发布游戏资料，不把这些数据当成可下载安装的目标。
- 终末地保留正式分卷和已开放的预下载差分，国服及 B 服的临时签名不入库。现有数据缺少稳定可用的完整卷及可独立核验的准确解包总量，不发布安装目标。

发布方需先取得可验证、受支持的介质再生成规范目标。旧索引与此协议不兼容；更新客户端后同步新版资源包，无须重装已有游戏。
