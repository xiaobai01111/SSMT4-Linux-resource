"""覆盖 HoYoPlay 分支未声明场景的目标。"""

import unittest

from Github.hoyoplay.sophon import component, patch_components


def build_component():
    # 发布方构建中存在这样的 mini 语音记录，channel 的场景表可能不包含它。
    delivery = {"url_prefix": "https://publisher.example/component/", "encryption": 0, "compression": 1}
    manifest = {"id": "manifest_id", "checksum": "a" * 32, "compressed_size": "17", "uncompressed_size": "24"}
    return {"category_id": "42", "category_name": "mini 语音", "matching_field": "mini-zh-cn",
            "manifest_download": delivery, "diff_download": delivery, "manifest": manifest}


class SophonTests(unittest.TestCase):
    def test_component_missing_from_scenario_list_is_preserved(self):
        result = component(build_component(), {})
        self.assertEqual(result["matchingField"], "mini-zh-cn")
        self.assertEqual(result["scenarios"], [])
        self.assertIsNone(result["categoryType"])
        self.assertEqual(result["manifest"]["url"], "https://publisher.example/component/manifest_id")
        self.assertEqual(result["manifest"]["unpackedMd5"], "a" * 32)

    def test_patch_only_lists_advertised_source_versions(self):
        # 同一份索引引用多个旧版，但只有发布分支仍列出的源版可进入本轮快照。
        candidate = build_component()
        candidate["stats"] = {"1.0": dict(compressed_size="12"), "0.9": dict(compressed_size="20")}
        patches = patch_components([candidate], {}, {"1.0"})
        self.assertEqual(set(patches[0]["fromVersions"]), {"1.0"})
        self.assertEqual(patches[0]["fromVersions"]["1.0"]["compressedBytes"], 12)
