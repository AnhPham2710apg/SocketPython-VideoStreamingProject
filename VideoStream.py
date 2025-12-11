import os

class VideoStream:
    def __init__(self, filename):
        self.filename = filename
        try:
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
        Quét toàn bộ file một lần để tìm vị trí Start/End của các frame.
        Sử dụng byte searching để đạt tốc độ tối đa.
        """
        print(f"Bắt đầu indexing file: {self.filename}...")
        
        # Đọc toàn bộ file vào RAM để tìm kiếm cực nhanh
        # (Lưu ý: Với file > 500MB nên dùng phương pháp đọc chunk, 
        # nhưng với bài tập Socket thì đọc hết là cách nhanh nhất)
        self.file.seek(0)
        data = self.file.read()
        
        file_len = len(data)
        pos = 0
        
        # Các marker của JPEG
        soi_marker = b'\xff\xd8'  # Start of Image
        eoi_marker = b'\xff\xd9'  # End of Image
        
        while True:
            # 1. Tìm điểm bắt đầu (SOI)
            start_pos = data.find(soi_marker, pos)
            if start_pos == -1:
                break # Không còn frame nào
            
            # 2. Tìm điểm kết thúc (EOI) từ vị trí bắt đầu
            end_pos = data.find(eoi_marker, start_pos)
            if end_pos == -1:
                break # File lỗi hoặc bị cắt cụt
            
            # Tính độ dài frame (bao gồm cả marker kết thúc)
            # EOI dài 2 byte nên cần +2 vào vị trí tìm thấy
            frame_length = (end_pos + 2) - start_pos
            
            # Lưu vào index: (offset, length)
            self.frame_index.append((start_pos, frame_length))
            
            # Cập nhật vị trí tìm kiếm tiếp theo
            pos = end_pos + 2
            
        print(f"Indexing hoàn tất. Tìm thấy {len(self.frame_index)} frames.")
        
        # Xóa dữ liệu tạm khỏi RAM
        del data
        # Reset con trỏ file về đầu
        self.file.seek(0)

    def nextFrame(self):
        """
        Lấy frame tiếp theo dựa trên index đã tạo.
        Tốc độ cực nhanh vì chỉ Seek và Read.
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