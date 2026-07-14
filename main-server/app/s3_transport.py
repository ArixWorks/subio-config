import asyncio
import time
import uuid

import aioboto3
from botocore.config import Config
from botocore.exceptions import ClientError


class S3FallbackStore:
    def __init__(
        self,
        *,
        endpoint: str,
        region: str,
        bucket: str,
        access_key: str,
        secret_key: str,
    ) -> None:
        self._bucket = bucket
        self._session = aioboto3.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )
        self._client_options = {
            "endpoint_url": endpoint,
            "config": Config(
                signature_version="s3v4",
                retries={"max_attempts": 2, "mode": "standard"},
                connect_timeout=2,
                read_timeout=2,
            ),
        }

    async def submit_and_wait(self, job_id: uuid.UUID, envelope: str, timeout: float) -> str:
        random_part = uuid.uuid4().hex
        pending_key = f"pending/{int(time.time())}_{random_part}_{job_id}.enc"
        result_key = f"results/{job_id}.enc"
        async with self._session.client("s3", **self._client_options) as client:
            await client.put_object(
                Bucket=self._bucket,
                Key=pending_key,
                Body=envelope.encode(),
                ContentType="application/octet-stream",
                ServerSideEncryption="AES256",
                Metadata={"job-id": str(job_id)},
            )
            deadline = asyncio.get_running_loop().time() + timeout
            while asyncio.get_running_loop().time() < deadline:
                try:
                    response = await client.get_object(Bucket=self._bucket, Key=result_key)
                    value = (await response["Body"].read()).decode()
                    await client.delete_object(Bucket=self._bucket, Key=result_key)
                    return value
                except ClientError as exc:
                    if exc.response.get("Error", {}).get("Code") not in {
                        "NoSuchKey",
                        "404",
                        "NotFound",
                    }:
                        raise
                    await asyncio.sleep(0.5)
            raise TimeoutError("S3 result deadline exceeded")
