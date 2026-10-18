import logging
from typing import List, Dict, Any
import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class S3Service:
    """Encapsulates all AWS S3 multipart upload operations."""

    def __init__(self) -> None:
        self.bucket_name = settings.s3_bucket_name
        self.region = settings.aws_region

        # Backend client: used for server-side S3 calls (create, list parts, complete, abort)
        self.s3_client = boto3.client(
            "s3",
            endpoint_url=settings.aws_endpoint_url,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            region_name=self.region,
            config=Config(signature_version="s3v4"),
        )

        # Public client: used specifically to generate presigned URLs that external clients can reach
        self.public_s3_client = boto3.client(
            "s3",
            endpoint_url=settings.aws_public_endpoint_url,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            region_name=self.region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def ensure_bucket_and_cors(self) -> None:
        """Create the S3 bucket if it doesn't exist and configure CORS exposing ETag."""
        try:
            self.s3_client.head_bucket(Bucket=self.bucket_name)
            logger.info("S3 Bucket '%s' already exists.", self.bucket_name)
        except ClientError:
            logger.info("Creating S3 Bucket '%s'...", self.bucket_name)
            create_bucket_kwargs = {"Bucket": self.bucket_name}
            if self.region != "us-east-1":
                create_bucket_kwargs["CreateBucketConfiguration"] = {
                    "LocationConstraint": self.region
                }
            self.s3_client.create_bucket(**create_bucket_kwargs)

        # Configure CORS so browser clients can read the returned ETag header
        cors_configuration = {
            "CORSRules": [
                {
                    "AllowedHeaders": ["*"],
                    "AllowedMethods": ["GET", "PUT", "POST", "HEAD"],
                    "AllowedOrigins": ["*"],
                    "ExposeHeaders": ["ETag"],
                    "MaxAgeSeconds": 3600,
                }
            ]
        }
        self.s3_client.put_bucket_cors(
            Bucket=self.bucket_name,
            CORSConfiguration=cors_configuration,
        )
        logger.info("Configured CORS on bucket '%s' with exposed ETag.", self.bucket_name)

    def create_multipart_upload(self, object_key: str, content_type: str = "video/mp4") -> str:
        """Initiate an S3 multipart upload session and return the AWS s3_upload_id."""
        response = self.s3_client.create_multipart_upload(
            Bucket=self.bucket_name,
            Key=object_key,
            ContentType=content_type,
        )
        return response["UploadId"]

    def generate_presigned_part_url(
        self,
        object_key: str,
        s3_upload_id: str,
        part_number: int,
        expires_in: int = 3600,
    ) -> str:
        """Generate a short-lived presigned PUT URL for uploading a specific part."""
        return self.public_s3_client.generate_presigned_url(
            ClientMethod="upload_part",
            Params={
                "Bucket": self.bucket_name,
                "Key": object_key,
                "UploadId": s3_upload_id,
                "PartNumber": part_number,
            },
            ExpiresIn=expires_in,
        )

    def list_all_uploaded_parts(
        self,
        object_key: str,
        s3_upload_id: str,
    ) -> List[Dict[str, Any]]:
        """
        List all confirmed parts stored in S3 for an upload session.
        Handles S3 pagination (IsTruncated and NextPartNumberMarker) because
        AWS S3 returns a maximum of 1,000 parts per ListParts call.
        """
        parts = []
        part_number_marker = 0

        while True:
            params: Dict[str, Any] = {
                "Bucket": self.bucket_name,
                "Key": object_key,
                "UploadId": s3_upload_id,
            }
            if part_number_marker > 0:
                params["PartNumberMarker"] = part_number_marker

            response = self.s3_client.list_parts(**params)

            if "Parts" in response:
                for part in response["Parts"]:
                    parts.append(
                        {
                            "PartNumber": part["PartNumber"],
                            "ETag": part["ETag"],
                            "Size": part["Size"],
                        }
                    )

            if response.get("IsTruncated", False):
                part_number_marker = response.get("NextPartNumberMarker")
            else:
                break

        return parts

    def complete_multipart_upload(
        self,
        object_key: str,
        s3_upload_id: str,
        parts: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Assemble all uploaded parts into the final S3 object.
        Parts must be a list of dicts with 'PartNumber' and 'ETag' sorted by PartNumber.
        """
        # AWS S3 strictly requires parts to be sorted in ascending order of PartNumber
        sorted_parts = sorted(
            [{"PartNumber": p["PartNumber"], "ETag": p["ETag"]} for p in parts],
            key=lambda x: x["PartNumber"],
        )

        return self.s3_client.complete_multipart_upload(
            Bucket=self.bucket_name,
            Key=object_key,
            UploadId=s3_upload_id,
            MultipartUpload={"Parts": sorted_parts},
        )

    def abort_multipart_upload(self, object_key: str, s3_upload_id: str) -> None:
        """Abort the multipart upload and free up S3 storage for uploaded parts."""
        self.s3_client.abort_multipart_upload(
            Bucket=self.bucket_name,
            Key=object_key,
            UploadId=s3_upload_id,
        )

