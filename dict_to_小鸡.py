# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/dict_to_小鸡.py
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

import pandas as pd
import datetime
import os
import json
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from 精准提取 import main as extract_main

# 自定义列名到原始列名的映射字典
column_mapping = {
    '项目名称': 'project_name',
    '编号': '',
    '视频链接': 'aweme_id',  # 假设aweme_id可以构建视频链接
    '时间': 'create_time',
    '昵称': 'nickname',
    '评论内容': 'content',
    'UID': 'user_id',
    '抖音号': 'user_unique_id',
    '地区': 'ip_location',
    '简介': 'user_signature',
    '头像': 'avatar',
    'SECUID': 'sec_uid',
    '点赞数': 'like_count',
    '精准': ''
}

# 自定义列名列表
custom_columns = [
    '项目名称', '编号', '视频链接', '时间', '昵称', '评论内容', 'UID', '抖音号',
    '性别', '地区', '简介', '粉丝', '关注', '头像', 'SECUID', '点赞数', '精准'
]

def load_project_config():
    """
    从data.json加载项目配置（白名单/黑名单地区）

    返回:
        dict: 项目名称到配置的映射
    """
    config = {}
    try:
        with open("data.json", "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list) and len(data) > 0:
            config_item = data[0]
            if "items" in config_item:
                for item in config_item["items"]:
                    project_name = item.get("project", "")
                    if project_name:
                        config[project_name] = {
                            "白名单地区": item.get("白名单地区", ""),
                            "黑名单地区": item.get("黑名单地区", "")
                        }
    except Exception as e:
        print(f"加载项目配置失败: {e}")

    return config


def filter_by_region(row, project_name, project_config):
    """
    根据白名单/黑名单过滤数据

    参数:
        row: DataFrame的一行数据
        project_name: 项目名称
        project_config: 项目配置

    返回:
        bool: True表示保留，False表示过滤
    """
    地区 = row.get('地区', '').strip()

    白名单 = project_config.get(project_name, {}).get("白名单地区", "")
    黑名单 = project_config.get(project_name, {}).get("黑名单地区", "")

    # 白名单优先级 > 黑名单
    if 白名单:
        # 白名单不为空：只保留地区在白名单中的数据
        # 地区 not in 白名单 → 过滤掉
        白名单列表 = [x.strip() for x in 白名单.split(',') if x.strip()]
        if 地区 not in 白名单列表:
            return False
    elif 黑名单:
        # 只有白名单为空时才使用黑名单
        # 地区 in 黑名单 → 过滤掉
        黑名单列表 = [x.strip() for x in 黑名单.split(',') if x.strip()]
        if 地区 in 黑名单列表:
            return False

    return True


def convert_json_to_excel(json_file_path):
    """
    将JSON文件转换为Excel文件

    参数:
        json_file_path: str, JSON文件路径

    返回:
        str, 生成出的Excel文件路径
    """
    project_config = load_project_config()
    with open(json_file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 如果是字典格式，提取所有项目并添加项目名称列
    if isinstance(data, dict):
        all_items = []
        for project_name, items in data.items():
            if isinstance(items, list):
                for item in items:
                    # 可以在这里添加项目名称到item中，如果需要的话
                    item["project_name"] = project_name
                    all_items.append(item)
        df = pd.DataFrame(all_items).astype(str)
    else:
        # 兼容旧版列表格式
        df = pd.DataFrame(data).astype(str)

    # 按UID去重，合并评论内容
    if 'user_id' in df.columns and 'content' in df.columns:
        # 先将内容列转换为字符串
        df['content'] = df['content'].astype(str)

        # 按user_id分组，合并评论内容
        def merge_comments(group):
            # 取第一条记录的所有字段
            result = group.iloc[0].copy()
            # 合并所有评论内容
            all_comments = group['content'].tolist()
            # 用换行符连接
            merged_comments = '\n'.join(all_comments)
            # 分割成行
            comment_lines = merged_comments.split('\n')
            # 去重、删除空行，保持顺序
            seen = set()
            unique_lines = []
            for line in comment_lines:
                stripped_line = line.strip()
                if stripped_line and stripped_line not in seen:
                    seen.add(stripped_line)
                    unique_lines.append(stripped_line)
            # 重新用换行符连接
            result['content'] = '\n'.join(unique_lines)
            return result

        df = df.groupby('user_id').apply(merge_comments).reset_index(drop=True)

    # 创建新的DataFrame，包含所有自定义列名
    new_df = pd.DataFrame()

    # 遍历自定义列名，添加到新DataFrame
    for col in custom_columns:
        if col in column_mapping and column_mapping[col] in df.columns:
            # 如果有映射且原始列存在，则使用原始列数据
            if col == '视频链接':
                # 构建抖音视频链接
                new_df[col] = 'https://www.douyin.com/discover?modal_id=' + df[column_mapping[col]]
            elif col == '时间':
                # 转换时间字符串为指定格式
                try:
                    # 获取时间列
                    create_time_col = df[column_mapping[col]]

                    # 尝试转换为数字(时间戳)
                    create_time_numeric = pd.to_numeric(create_time_col, errors='coerce')

                    if create_time_numeric.notna().sum() > 0:
                        # 如果存在数字，优先作为时间戳处理 (假设是秒)
                        # 简单的启发式判断：如果是毫秒，通常很大
                        # 这里简单假设是秒，因为从JSON看是10位时间戳
                        new_df[col] = pd.to_datetime(create_time_numeric, unit='s', errors='coerce').dt.strftime('%Y-%m-%d %H:%M:%S')
                    else:
                        # 否则尝试作为字符串解析
                        new_df[col] = pd.to_datetime(create_time_col, errors='coerce').dt.strftime('%Y-%m-%d %H:%M:%S')

                    # 处理可能的NaT值
                    new_df[col] = new_df[col].fillna('')
                except Exception as e:
                    print(f"时间转换错误: {e}")
                    # 如果转换失败，使用空字符串
                    new_df[col] = ''
            elif col == '抖音号':
                # 抖音号优先使用user_unique_id，如果为空则使用short_user_id
                user_unique_id_col = df[column_mapping[col]].astype(str)
                # 检查short_user_id是否存在于原始数据中
                if 'short_user_id' in df.columns:
                    short_user_id_col = df['short_user_id'].astype(str)
                    # 使用where函数：如果user_unique_id不为空则使用它，否则使用short_user_id
                    new_df[col] = user_unique_id_col.where(user_unique_id_col != '', short_user_id_col)
                else:
                    # 如果short_user_id不存在，只使用user_unique_id
                    new_df[col] = user_unique_id_col
            else:
                # 将原始列转换为字符串类型
                new_df[col] = df[column_mapping[col]].astype(str)
        elif col == '精准':
            # 处理精准列，调用精准提取
            def extract_precise(row):
                try:
                    nickname = str(row.get('nickname', '')).strip()
                    user_unique_id = str(row.get('user_unique_id', '')).strip()
                    user_signature = str(row.get('user_signature', '')).strip()

                    text_parts = []
                    if nickname:
                        text_parts.append(nickname)
                    if user_unique_id:
                        text_parts.append(user_unique_id)
                    if user_signature:
                        text_parts.append(user_signature)

                    input_text = '\n'.join(text_parts)
                    if input_text.strip():
                        result = extract_main(input_text)
                        return result
                    return ''
                except Exception as e:
                    print(f"精准提取出错: {e}")
                    return ''

            new_df[col] = df.apply(extract_precise, axis=1)
        else:
            # 否则添加空字符串
            new_df[col] = ''

    if '项目名称' in new_df.columns and '地区' in new_df.columns:
        keep_mask = new_df.apply(
            lambda row: filter_by_region(row, row.get('项目名称', ''), project_config),
            axis=1
        )
        new_df = new_df[keep_mask]
        new_df = new_df.reset_index(drop=True)

    # 过滤近90天的数据

    # 将时间列转换为datetime对象以便比较
    new_df['时间_datetime'] = pd.to_datetime(new_df['时间'], format='%Y-%m-%d %H:%M:%S', errors='coerce')

    # 移除临时的datetime列
    if '时间_datetime' in new_df.columns:
        new_df = new_df.drop('时间_datetime', axis=1)

    # 确保所有列都是字符串类型
    new_df = new_df.astype(str)

    # 按UID去重，保留第一条记录
    # if 'UID' in new_df.columns:
    #    new_df = new_df.drop_duplicates(subset=['UID'], keep='first')

    # 按时间倒序排序（从新到旧）并过滤30天内的数据
    if '时间' in new_df.columns:
        # 将时间列转换为datetime类型以便排序和过滤
        new_df['时间_datetime_sort'] = pd.to_datetime(new_df['时间'], format='%Y-%m-%d %H:%M:%S', errors='coerce')

        # 计算30天前的日期
        thirty_days_ago = datetime.datetime.now() - datetime.timedelta(days=14)

        # 过滤只保留30天内的数据
        new_df = new_df[new_df['时间_datetime_sort'] >= thirty_days_ago]

        # 按时间倒序排序
        new_df = new_df.sort_values(by='时间_datetime_sort', ascending=False)
        # 移除临时的排序列
        new_df = new_df.drop('时间_datetime_sort', axis=1)
        # 重置索引
        new_df = new_df.reset_index(drop=True)

    # 重新添加自动排序的编号列（从1开始）
    new_df['编号'] = range(1, len(new_df) + 1)

    # 确保编号列是字符串类型
    new_df['编号'] = new_df['编号'].astype(str)

    # 处理可能的NaT和NaN，转换为空字符串
    new_df = new_df.replace(['NaT', 'nan', 'NaN'], '')

    # 创建导出文件目录（如果不存在）
    # 使用os.path模块处理路径，确保跨平台兼容性
    export_dir = os.path.join('.', '导出文件')
    if not os.path.exists(export_dir):
        os.makedirs(export_dir)

    # 生成Excel文件名
    base_name = os.path.splitext(os.path.basename(json_file_path))[0]
    excel_file_path = os.path.join(export_dir, f'{base_name}_export.xlsx')

    # 导出前删除项目名称列
    if '项目名称' in new_df.columns:
        new_df = new_df.drop('项目名称', axis=1)

    # 转为Excel
    new_df.to_excel(excel_file_path, index=False)

    return excel_file_path

if __name__ == "__main__":
    # 示例用法
    import sys
    if len(sys.argv) > 1:
        json_path = sys.argv[1]
        excel_path = convert_json_to_excel(json_path)
        print(f"Excel文件已生成: {excel_path}")
    else:
        print("请提供JSON文件路径作为参数")
