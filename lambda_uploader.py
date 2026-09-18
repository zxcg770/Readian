"""Lambda A：接收手机上传的音频，写入 S3 后立刻返回。

不做任何处理——处理交给 S3 事件触发的 Lambda B。
这样快捷指令 1 秒内就能拿到响应，不用在跑步机上举着手机干等。
"""
import base64
import os
import uuid
from datetime import datetime

import boto3

s3 = boto3.client("s3")

BUCKET = os.environ["BUCKET_NAME"]
SECRET = os.environ["UPLOAD_SECRET"]


def lambda_handler(event, context):
    # Function URL 是公开端点，用共享密钥挡住随机请求
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if headers.get("x-upload-secret") != SECRET:
        return {"statusCode": 403, "body": "forbidden"}

    body = event.get("body")
    if not body:
        return {"statusCode": 400, "body": "empty body"}

    # 二进制请求体会被 Function URL 编码成 base64
    audio = base64.b64decode(body) if event.get("isBase64Encoded") else body.encode()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    key = f"incoming/{stamp}-{uuid.uuid4().hex[:6]}.m4a"

    s3.put_object(Bucket=BUCKET, Key=key, Body=audio)
    print(f"已存入 s3://{BUCKET}/{key}（{len(audio)} bytes）")

    return {"statusCode": 200, "body": "ok"}
