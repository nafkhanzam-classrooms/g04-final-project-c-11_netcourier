# Panduan Pembuatan File Test Binary

Direktori ini digunakan untuk menyimpan file binary yang digunakan dalam pengujian upload/download aplikasi NetCourier. File-file ini di-ignore oleh git karena ukurannya yang besar.

Untuk membuat ulang file-file test ini di sistem Windows (PowerShell), gunakan perintah berikut:

```powershell
# 0 Byte (Edge Case)
fsutil file createnew test_0b.bin 0

# 1 KB (Small File)
fsutil file createnew test_1kb.bin 1024

# 65 KB (Chunk Boundary - if chunk is 64KB)
fsutil file createnew test_65kb.bin 66560

# 1 MB (Existing)
fsutil file createnew test_1mb.bin 1048576

# 5 MB (Existing)
fsutil file createnew test_5mb.bin 5242880

# 10 MB (Existing)
fsutil file createnew test_10mb.bin 10485760

# 25 MB (Media/Photo Equivalent)
fsutil file createnew test_25mb.bin 26214400

# 100 MB (Resume/Reliability Test)
fsutil file createnew test_100mb.bin 104857600

# 500 MB (Large Asset)
fsutil file createnew test_500mb.bin 524288000

# 1 GB (Stress/Memory Leak Test)
fsutil file createnew test_1gb.bin 1073741824
```

Pastikan untuk menjalankan perintah di atas di dalam direktori `tests/uploadbinarytest/`.
