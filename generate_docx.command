#!/bin/bash
# 一键生成周报 Word 文档
# 双击此文件即可运行

cd "$(dirname "$0")"

# 安装依赖（如果未安装）
pip3 install python-docx -q 2>/dev/null

# 生成 Word 文档
python3 generate_weekly_report.py

echo ""
echo "✅ 周报已生成！"
open "周报_2026年4月第4周.docx"
