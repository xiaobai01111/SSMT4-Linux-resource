"""终末地卷号和临时链接的离线回归测试。"""

import unittest

from Github.arknights_endfield.volumes import ordered_parts


def volume(number, query=""):
    # 测试卷名与生产格式相同，查询参数使用不对应实际地址的样例。
    return {"url": f"https://publisher.example/windows/archive.zip.{number:03}{query}",
            "md5": "a" * 32, "package_size": "1"}


class PublishTests(unittest.TestCase):
    def test_volumes_are_sorted_and_signatures_do_not_enter_package(self):
        parts = ordered_parts([volume(2), volume(1, "?auth_key=fixture")])
        self.assertEqual([part["path"] for part in parts], ["archive.zip.001", "archive.zip.002"])
        first, second = parts
        # 有时效的地址只能交付稳定定位信息，普通资源仍可直接下载。
        self.assertEqual(first["locator"], "https://publisher.example/windows/archive.zip.001")
        self.assertTrue(first["urlRequiresRefresh"])
        self.assertNotIn("url", first)
        self.assertEqual(second["url"], "https://publisher.example/windows/archive.zip.002")

    def test_missing_volume_rejects_release(self):
        with self.assertRaisesRegex(ValueError, "缺卷"):
            ordered_parts([volume(1), volume(3)])
