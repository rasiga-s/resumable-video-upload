#!/usr/bin/env python3
"""
Resumable Video Upload Client Simulator.

Demonstrates the YouTube-style upload lifecycle:
1. Initiates an upload session.
2. Uploads initial parts directly to S3 via presigned URLs.
3. Simulates a Wi-Fi disconnect / network failure.
4. Reconnects and queries the backend for authoritative resume status.
5. Uploads only the missing parts.
6. Assembles the final video and tests idempotent completion.
"""

import os
import sys
import time
import requests

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
USER_ID = "creator_123"
TEST_FILENAME = "test_large_video.mp4"
# 25 MB test file: 5 parts of 5 MB each (5 MB is AWS S3's minimum part size)
PART_SIZE = 5 * 1024 * 1024  # 5 MB
FILE_SIZE = 25 * 1024 * 1024  # 25 MB


def create_dummy_video_file(filename: str, size_bytes: int) -> None:
    """Creates a local dummy video file for testing if it doesn't already exist."""
    if os.path.exists(filename) and os.path.getsize(filename) == size_bytes:
        print(f"📁 Reusing existing test file: {filename} ({size_bytes // (1024 * 1024)} MB)")
        return

    print(f"📁 Creating dummy test file: {filename} ({size_bytes // (1024 * 1024)} MB)...")
    with open(filename, "wb") as f:
        # Write chunks of 1MB to avoid allocating 25MB all at once
        chunk = b"\x00" * (1024 * 1024)
        for _ in range(size_bytes // (1024 * 1024)):
            f.write(chunk)
    print("✅ Dummy test file created successfully.")


def upload_part_to_s3(
    file_path: str,
    upload_id: str,
    part_number: int,
    part_size: int,
    user_id: str,
) -> None:
    """Fetches a presigned URL and uploads the raw byte chunk directly to S3."""
    # 1. Ask FastAPI for a presigned S3 PUT URL
    url_resp = requests.get(
        f"{API_BASE_URL}/uploads/{upload_id}/parts/{part_number}/url",
        params={"user_id": user_id},
    )
    url_resp.raise_for_status()
    presigned_url = url_resp.json()["presigned_url"]

    # 2. Slice the file to get the exact part bytes
    offset = (part_number - 1) * part_size
    with open(file_path, "rb") as f:
        f.seek(offset)
        part_bytes = f.read(part_size)

    # 3. Stream bytes directly to S3 / LocalStack
    put_resp = requests.put(presigned_url, data=part_bytes)
    put_resp.raise_for_status()
    print(f"   ⬆️  Part {part_number} successfully uploaded to S3 ({len(part_bytes) // (1024 * 1024)} MB)")


def main() -> None:
    print("=" * 70)
    print("   🎬 YOUTUBE-STYLE RESUMABLE UPLOAD SIMULATOR (AWS S3 + LOCALSTACK)")
    print("=" * 70)

    # Step 0: Verify API server is reachable
    print("\n[Step 0] Verifying FastAPI backend...")
    try:
        health = requests.get(f"{API_BASE_URL}/health", timeout=3)
        if health.status_code != 200:
            print(f"❌ Backend returned HTTP {health.status_code}. Is Docker Compose running?")
            sys.exit(1)
        print("✅ Backend is healthy and running.")
    except Exception as e:
        print(f"❌ Cannot connect to {API_BASE_URL}: {e}")
        print("👉 Make sure docker compose is running: `docker compose up -d`")
        sys.exit(1)

    # Step 1: Create local dummy video file
    create_dummy_video_file(TEST_FILENAME, FILE_SIZE)

    # Step 2: Initiate upload session
    print("\n[Step 1] Initiating upload session with FastAPI...")
    init_payload = {
        "filename": TEST_FILENAME,
        "file_size": FILE_SIZE,
        "part_size": PART_SIZE,
        "user_id": USER_ID,
    }
    init_resp = requests.post(f"{API_BASE_URL}/uploads", json=init_payload)
    init_resp.raise_for_status()
    init_data = init_resp.json()

    upload_id = init_data["upload_id"]
    total_parts = init_data["total_parts"]
    print(f"✅ Session created:")
    print(f"   - Upload ID:   {upload_id}")
    print(f"   - S3 Upload ID: {init_data['s3_upload_id']}")
    print(f"   - Total Parts: {total_parts} (Part size: {init_data['part_size'] // (1024 * 1024)} MB)")

    # Step 3: Upload initial parts (Parts 1 & 2)
    print("\n[Step 2] Uploading initial chunks (Parts 1 & 2)...")
    for part_num in [1, 2]:
        upload_part_to_s3(TEST_FILENAME, upload_id, part_num, PART_SIZE, USER_ID)

    # Step 4: Simulate Wi-Fi drop / network disconnection
    print("\n" + "!" * 70)
    print("⚠️  [SIMULATION] WI-FI DROPPED! Connection severed mid-upload!")
    print("   Parts 1 & 2 were saved in S3, but Part 3 was never transmitted.")
    print("   The TCP connection memory is wiped out.")
    print("!" * 70)

    print("\n⏳ Simulating 2 seconds of network outage...")
    time.sleep(2)

    # Step 5: Wi-Fi Reconnects & queries server for resume status
    print("\n[Step 3] Wi-Fi restored! Querying server for authoritative resume status...")
    resume_resp = requests.get(
        f"{API_BASE_URL}/uploads/{upload_id}/resume",
        params={"user_id": USER_ID},
    )
    resume_resp.raise_for_status()
    resume_data = resume_resp.json()

    print(f"✅ Server reported confirmed progress (Source of Truth):")
    print(f"   - Confirmed parts stored in S3: {resume_data['uploaded_parts_count']}/{total_parts}")
    print(f"   - Uploaded bytes: {resume_data['uploaded_bytes']} / {resume_data['file_size']} ({resume_data['percentage_complete']}%)")
    print(f"   - Missing parts to upload: {resume_data['missing_part_numbers']}")

    # Step 6: Resume upload — ONLY send missing parts!
    missing_parts = resume_data["missing_part_numbers"]
    print(f"\n[Step 4] Resuming upload from missing parts: {missing_parts}...")
    for part_num in missing_parts:
        upload_part_to_s3(TEST_FILENAME, upload_id, part_num, PART_SIZE, USER_ID)

    # Step 7: Complete the upload
    print("\n[Step 5] All parts uploaded. Requesting final assembly in S3...")
    complete_resp = requests.post(
        f"{API_BASE_URL}/uploads/{upload_id}/complete",
        params={"user_id": USER_ID},
    )
    complete_resp.raise_for_status()
    complete_data = complete_resp.json()

    print("🎉 SUCCESS! Video assembled and finalized:")
    print(f"   - Video ID:   {complete_data['video_id']}")
    print(f"   - S3 Key:     {complete_data['object_key']}")
    print(f"   - Status:     {complete_data['status']}")
    print(f"   - Message:    {complete_data['message']}")

    # Step 8: Verify Idempotency (calling complete again)
    print("\n[Step 6] Testing Idempotency (calling /complete a second time)...")
    retry_resp = requests.post(
        f"{API_BASE_URL}/uploads/{upload_id}/complete",
        params={"user_id": USER_ID},
    )
    retry_resp.raise_for_status()
    retry_data = retry_resp.json()
    print(f"✅ Idempotent Response: {retry_data['message']}")

    # Clean up dummy file
    if os.path.exists(TEST_FILENAME):
        os.remove(TEST_FILENAME)
        print(f"\n🧹 Cleaned up temporary test file: {TEST_FILENAME}")

    print("\n" + "=" * 70)
    print("   🏁 RESUMABLE UPLOAD TEST COMPLETED WITH 100% SUCCESS!")
    print("=" * 70)


if __name__ == "__main__":
    main()

