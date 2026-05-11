import pandas as pd
import datetime
import os
import json

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
    '点赞数': 'like_count'
}

# 自定义列名列表
custom_columns = [
    '编号', '视频链接', '时间', '昵称', '评论内容', 'UID', '抖音号', 
    '性别', '地区', '简介', '粉丝', '关注', '头像', 'SECUID', '点赞数'
]

def convert_json_to_excel(json_file_path):
    """
    将JSON文件转换为Excel文件
    
    参数:
        json_file_path: str, JSON文件路径
    
    返回:
        str, 生成出的Excel文件路径
    """
    # 读取JSON文件
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
        else:
            # 否则添加空字符串
            new_df[col] = ''
    
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