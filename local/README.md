# Mac 真实引擎辅助脚本与对局配置

这里是在 Apple Silicon Mac 上用 Android Studio AVD 跑真实 native 引擎的辅助脚本，以及已交付对局的配置。

## 获取代码

```bash
git clone https://github.com/huiyuanXP/FirstLight_CR.git
cd FirstLight_CR
git checkout claude/project-thread-6u6fhr
```

checkpoint 已在仓库的 `checkpoints/` 里，见 `checkpoints/manifest.json`。

## Python 环境（Python 3.12）

推荐用 uv：

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

也可以用普通 venv：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

`requirements.txt` 锁定了主要依赖。`local/requirements-lock.macos-arm64.txt` 是交付对局时那台 macOS arm64 机器的完整依赖版本（Python 3.12.13），用于核对环境。其他系统按 `requirements.txt` 安装即可；GPU 机器需要另选与 CUDA 匹配的 torch 安装源。

只读取已交付的数据（json / jsonl / gz）不需要这个环境。

## 文件

| 文件 | 用途 |
| --- | --- |
| `prepare_mac_engine.sh` | 从已安装并更新好 Null's Royale 15.535.13 的 AVD 构建离线 APK，克隆出 `Pixel_9_FirstLight`，并启动、认证两个引擎（主机端口 26789/26790） |
| `run_mac_selfplay.sh` | 引擎未运行时启动 AVD 并认证引擎，然后跑一局（输出目录自动带时间戳） |
| `policy_episode_with_config.py` | 用 JSON 中的 MatchConfig 字段（卡组、形态等）运行 `tools/policy_episode.py` |
| `hog26-vs-golem.json` | 2.6 速猪 对 冰人卡组 |
| `hog26-vs-xbow30.json` | 2.6 速猪 对 3.0 X-Bow（`focus_decks_20.json` 第 6 名） |
| `hog26-mirror.json` | 双方都用 2.6 速猪 |

## 已交付对局的命令

引擎就绪后（端口 26789 可连接），在仓库根目录执行。

episode 001：General 对 IL，代码默认卡组

```bash
.venv/bin/python tools/policy_episode.py --engine native --port 26789 --checkpoint-0 checkpoints/General --checkpoint-1 checkpoints/IL --seed 1 --sample --dump-observations --output runs/real-episode-001
```

episode 002：速猪专精 2（2.6 速猪）对 General（3.0 X-Bow）

```bash
.venv/bin/python local/policy_episode_with_config.py local/hog26-vs-xbow30.json --engine native --port 26789 --checkpoint-0 checkpoints/2_6hog_expert/hog26-specialist2.pt --checkpoint-1 checkpoints/General/checkpoint-step-00000460.pt --seed 1 --sample --dump-observations --output runs/real-episode-002-hog26-vs-xbow30
```

镜像局：速猪专精 2 对 General，双方 2.6 速猪

```bash
.venv/bin/python local/policy_episode_with_config.py local/hog26-mirror.json --engine native --port 26789 --checkpoint-0 checkpoints/2_6hog_expert/hog26-specialist2.pt --checkpoint-1 checkpoints/General/checkpoint-step-00000460.pt --seed 1 --sample --dump-observations --output runs/real-episode-002-hog-vs-general
```

## 重新跑对局需要自建引擎

真实对局需要自己构建的离线引擎：自行提供原版 Null's Royale 15.535.13 APK，按 `docs/getting_started.md` 准备，再在 Mac 上运行 `local/prepare_mac_engine.sh`。APK 和 AVD 镜像不可分发，仓库中不包含。
