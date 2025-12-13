import os
import mmap  # Thư viện quan trọng để xử lý file lớn

class VideoStream:
    def __init__(self, filename):
        self.filename = filename
        try:
            # Mở file chế độ đọc binary
            self.file = open(filename, 'rb')
        except:
            raise IOError
        self.frameNum = 0
        
        # Danh sách chứa tuple (offset, length) cho từng frame
        self.frame_index = [] 
        
        # Xây dựng chỉ mục ngay khi khởi tạo
        self._build_index_fast()

    def _build_index_fast(self):
        """
        Quét file bằng Memory Mapped File (mmap).
        Hệ điều hành sẽ chỉ load các trang nhớ (pages) cần thiết vào RAM 
        thay vì load toàn bộ file. Hiệu quả tuyệt đối với file lớn.
        """
        # Kiểm tra kích thước file để tránh lỗi mmap với file rỗng
        self.file.seek(0, os.SEEK_END)
        file_size = self.file.tell()
        self.file.seek(0)
        
        if file_size == 0:
            return

        # Tạo memory-mapped file từ file descriptor
        # access=mmap.ACCESS_READ: Chỉ map để đọc, an toàn và tối ưu
        with mmap.mmap(self.file.fileno(), length=0, access=mmap.ACCESS_READ) as mm:
            pos = 0
            
            # Các marker của JPEG
            soi_marker = b'\xff\xd8'  # Start of Image
            eoi_marker = b'\xff\xd9'  # End of Image
            
            while True:
                # Tìm SOI marker trên vùng nhớ ảo (nhanh như tìm trên RAM)
                start_pos = mm.find(soi_marker, pos)
                if start_pos == -1:
                    break 
                
                # Tìm EOI marker
                end_pos = mm.find(eoi_marker, start_pos)
                if end_pos == -1:
                    break 
                
                # Tính độ dài frame
                frame_length = (end_pos + 2) - start_pos
                
                # Lưu vào index: (offset, length)
                self.frame_index.append((start_pos, frame_length))
                
                # Cập nhật vị trí tìm kiếm tiếp theo
                pos = end_pos + 2

        # Lưu ý: mmap tự động đóng khi thoát khỏi khối 'with'.
        # File gốc (self.file) vẫn mở để dùng cho hàm nextFrame().
        
        # Reset con trỏ file về đầu để chuẩn bị cho việc đọc frame
        self.file.seek(0)

    def nextFrame(self):
        """
        Lấy frame tiếp theo dựa trên index đã tạo.
        Dùng seek() và read() truyền thống vì hiệu quả cho việc đọc tuần tự khối nhỏ.
        """
        if self.frameNum < len(self.frame_index):
            offset, length = self.frame_index[self.frameNum]
            
            # Nhảy tới vị trí frame
            self.file.seek(offset)
            
            # Đọc đúng độ dài frame
            data = self.file.read(length)
            
            self.frameNum += 1
            return data
        else:
            return None

    def frameNbr(self):
        """Get frame number."""
        return self.frameNum