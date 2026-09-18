"""Lambda B：S3 事件触发的处理管道。

只做一件事：把 Phase 1 已经验证过的 process() 包一层壳。
业务逻辑完全没动——这正是当初把 process() 写成纯函数的回报。
"""
import os
import tempfile
import urllib.parse

import boto3

from src.main import process

s3 = boto3.client("s3")


def process_handler(event, context):
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        # S3 事件里的 key 是 URL 编码的，中文和空格都会被转义，必须解码
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        print(f"收到文件：s3://{bucket}/{key}")

        suffix = os.path.splitext(key)[1] or ".m4a"
        # Lambda 只有 /tmp 可写
        fd, path = tempfile.mkstemp(suffix=suffix, dir="/tmp")
        os.close(fd)

        try:
            s3.download_file(bucket, key, path)
            process(path)
        except Exception as e:
            # 打到 CloudWatch，同时重新抛出让 Lambda 记为失败便于告警
            print(f"处理失败：{type(e).__name__}: {e}")
            raise
        finally:
            if os.path.exists(path):
                os.remove(path)

    return {"statusCode": 200}
