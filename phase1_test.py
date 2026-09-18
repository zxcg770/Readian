# 按顺序单独调试每一环


# 1. GitHub 连通性
from src import github_client
print(github_client.list_dir("Readian/书籍"))


# 2. 当前在读推导
from src import vault
print(vault.get_current_book()["book"])


# 3. 转写效果（重点看专有名词准确率）
from src import transcribe
print(transcribe.transcribe("audio/test.m4a", "德米安", ["德米安", "辛克莱"]))


# 4. 分类准确性
from src import classify
print(classify.classify("这句话我不太理解，什么叫亚伯拉克萨斯", "德米安（果麦经典）"))


# 5. 单独验证写入（不碰音频，直接构造文本）
from src import vault
book = vault.get_current_book()
print("目标文件：", book["path"])

vault.save_to_book(book, "疑问与感悟", "- 这是一条测试疑问\n  - ⏱ 手动测试")
print("写入完成，去 GitHub 网页看看")


# 6. 验证待整理分支
vault.save_to_inbox("- 归属不明的测试内容\n  - ⏱ 手动测试")
print("待整理写入完成，去 GitHub 网页看看")


# 7. 跑完整流程 bash
# python -m src.main audio/test.m4a