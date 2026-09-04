# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/精准提取.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

import re

def main(text:str):
    """
    从文本列表中提取有效中国大陆手机号（去重、过滤无效号段）
    :param text: 文本列表（如网页内容、聊天记录、文档文本等）
    :return: 有效手机号列表（去重）
    """
    result = extract_mobile_from_text(text)
    # 去重
    result = list(set(result))
    print(",".join(result))
    return ",".join(result)

def extract_mobile_from_text(full_text):
    """
    从全文任意位置提取有效中国大陆手机号（去重、过滤无效号段）
    :param full_text: 任意文本（如网页内容、聊天记录、文档文本等）
    :return: 有效手机号列表（去重）
    """
    # 核心正则：全文匹配，精准号段，11位纯数字，排除嵌在长数字中的情况
    mobile_regex = r'(?<!\d)(13[0-9]|14[5-9]|15[0-35-9]|16[6]|17[0-8]|18[0-9]|19[0-35-68-9])\d{8}(?!\d)'
    # 查找全文中所有匹配项
    all_matches = re.findall(mobile_regex, full_text)
    # 注意：findall匹配带分组的正则时，返回的是分组内容，需拼接完整号码
    # 修正：调整正则为非捕获分组，直接返回完整号码
    mobile_regex_fixed = r'(?<!\d)(?:13[0-9]|14[5-9]|15[0-35-9]|16[6]|17[0-8]|18[0-9]|19[0-35-68-9])\d{8}(?!\d)'
    valid_mobiles = re.findall(mobile_regex_fixed, full_text)
    # 去重并返回
    return list(set(valid_mobiles))

# 测试：文本中手机号出现在任意位置
if __name__ == "__main__":
    test_content = """
    这是一段包含手机号的文本，手机号可能在开头：13912345678，
    也可能在中间：联系人电话18687654321，还可能在末尾：紧急联系19900008888。
    无效号码：14412345678（号段无效）、12345678901（号段无效）、138123456789（长度超11位）。
    """
    while True:

        result = main(test_content)
        print("全文提取的有效手机号：", result)
        test_content = input("请输入新的文本：")
