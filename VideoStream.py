class VideoStream:
	def __init__(self, filename):
		self.filename = filename
		try:
			self.file = open(filename, 'rb')
		except:
			raise IOError
		self.frameNum = 0
		
	def nextFrame(self):
		"""SỬA ĐỔI: Get next frame với header 10 byte."""
  
		# Đọc 10 byte cho độ dài khung hình
		data = self.file.read(10) # <-- SỬA TỪ 5 LÊN 10
		
		if data: 
			try:
				# Chuyển đổi 10 byte (dạng text) thành số nguyên
				framelength = int(data)
			except ValueError:
				# Xảy ra nếu file bị hỏng hoặc hết file
				print("Lỗi: Không thể đọc độ dài khung hình từ header.")
				return None
							
			# Đọc khung hình với độ dài chính xác
			data = self.file.read(framelength)
			self.frameNum += 1
		return data
		
	def frameNbr(self):
		"""Get frame number."""
		return self.frameNum