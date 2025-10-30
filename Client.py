from tkinter import *
import tkinter.messagebox
from PIL import Image, ImageTk
import socket, threading, sys, traceback, os

from RtpPacket import RtpPacket

CACHE_FILE_NAME = "cache-"
CACHE_FILE_EXT = ".jpg"

# Header JPEG giờ là 8 byte (phải khớp với ServerWorker)
JPEG_HEADER_SIZE = 8

class Client:
	# THÊM MỚI: Đặt chiều rộng tối đa cho khung hình video
	# Cửa sổ sẽ co lại vừa với kích thước này
	MAX_DISPLAY_WIDTH = 1024 
 
	INIT = 0
	READY = 1
	PLAYING = 2
	state = INIT
	
	SETUP = 0
	PLAY = 1
	PAUSE = 2
	TEARDOWN = 3
	
	def __init__(self, master, serveraddr, serverport, rtpport, filename):
		self.master = master
		self.master.protocol("WM_DELETE_WINDOW", self.handler)
		self.createWidgets()
		self.serverAddr = serveraddr
		self.serverPort = int(serverport)
		self.rtpPort = int(rtpport)
		self.fileName = filename
		self.rtspSeq = 0
		self.sessionId = 0
		self.requestSent = -1
		self.teardownAcked = 0
		self.connectToServer()
		self.frameNbr = 0
  
		self.elapsed_time = 0
		self.timer_running = False
  
		# THÊM MỚI: Bộ đệm tái hợp
		self.reassembly_buffer = {}
		# THÊM MỚI: Biến lưu kích thước khung hình dự kiến
		self.expected_frame_size = 0
		
	def createWidgets(self):
		"""Xây dựng giao diện GUI."""
		# HÀNG 2: CÁC NÚT
		self.setup = Button(self.master, width=20, padx=3, pady=3)
		self.setup["text"] = "Setup"
		self.setup["command"] = self.setupMovie
		self.setup.grid(row=2, column=0, padx=2, pady=2)
		
		self.start = Button(self.master, width=20, padx=3, pady=3)
		self.start["text"] = "Play"
		self.start["command"] = self.playMovie
		self.start.grid(row=2, column=1, padx=2, pady=2)
		
		self.pause = Button(self.master, width=20, padx=3, pady=3)
		self.pause["text"] = "Pause"
		self.pause["command"] = self.pauseMovie
		self.pause.grid(row=2, column=2, padx=2, pady=2)
		
		self.teardown = Button(self.master, width=20, padx=3, pady=3)
		self.teardown["text"] = "Teardown"
		self.teardown["command"] =  self.exitClient
		self.teardown.grid(row=2, column=3, padx=2, pady=2)
		
		# HÀNG 0: KHUNG HÌNH PHIM
		# SỬA ĐỔI: Xóa 'height=19' để Label tự co giãn theo ảnh đã resize
		self.label = Label(self.master)
		self.label.grid(row=0, column=0, columnspan=4, sticky=W+E+N+S, padx=5, pady=5)

		# HÀNG 1: BỘ ĐẾM GIỜ
		self.timer_label = Label(self.master, text="00:00", font=("Arial", 12))
		self.timer_label.grid(row=1, column=0, columnspan=4, padx=5, pady=2)

		# Trạng thái ban đầu
		self.setup["state"] = "normal"
		self.start["state"] = "disabled"
		self.pause["state"] = "disabled"
		self.teardown["state"] = "disabled"
	
	def setupMovie(self):
		if self.state == self.INIT:
			self.sendRtspRequest(self.SETUP)
	
	def exitClient(self):
		self.sendRtspRequest(self.TEARDOWN) 	

		self.timer_running = False
		self.elapsed_time = 0
		try:
			self.timer_label.config(text="00:00")
		except TclError:
			pass 

		if os.path.exists(CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT):
			os.remove(CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT)
		
		self.master.destroy()

	def pauseMovie(self):
		if self.state == self.PLAYING:
			self.sendRtspRequest(self.PAUSE)
   
			self.timer_running = False
	
	def playMovie(self):
		if self.state == self.READY:
			threading.Thread(target=self.listenRtp).start()
			self.playEvent = threading.Event()
			self.playEvent.clear()
			self.sendRtspRequest(self.PLAY)

			self.timer_running = True
			self.update_timer()
	
	def listenRtp(self):
		"""SỬA ĐỔI HOÀN TOÀN: Logic tái hợp gói tin CÓ KIỂM TRA ĐỘ HOÀN CHỈNH."""
		while True:
			try:
				data = self.rtpSocket.recv(20480)
				if data:
					rtpPacket = RtpPacket()
					rtpPacket.decode(data)
					
					currFrameNbr = rtpPacket.seqNum()
					marker = rtpPacket.marker() # Lấy Marker bit
					payload = rtpPacket.getPayload()

					# Đảm bảo payload đủ lớn để chứa header 8-byte
					if len(payload) < JPEG_HEADER_SIZE:
						print(f"Runt packet received for frame {currFrameNbr}. Discarding.")
						continue # Bỏ qua gói tin quá nhỏ

					# SỬA ĐỔI: Đọc header 8-byte
					# Bytes 0-3: Fragment Offset
					offset = int.from_bytes(payload[0:4], byteorder='big')
					# Bytes 4-7: Total Frame Size
					total_size = int.from_bytes(payload[4:8], byteorder='big')
					
					fragment_data = payload[JPEG_HEADER_SIZE:]

					# Logic tái hợp
					if currFrameNbr > self.frameNbr:
						# Đây là mảnh đầu tiên của một frame mới
						self.frameNbr = currFrameNbr
						print("Current Seq Number:", currFrameNbr)
						self.reassembly_buffer = {} # Xóa bộ đệm cũ
						self.expected_frame_size = total_size # Lưu kích thước dự kiến
						self.reassembly_buffer[offset] = fragment_data
					
					elif currFrameNbr == self.frameNbr:
						# Đây là một mảnh của frame hiện tại
						# Chỉ thêm nếu frame này chưa bị hỏng (expected_size > 0)
						if self.expected_frame_size > 0:
							self.reassembly_buffer[offset] = fragment_data

					# Nếu đây là mảnh cuối cùng (M=1) và bộ đệm có dữ liệu
					if marker == 1 and self.reassembly_buffer:
						
						# SỬA ĐỔI: KIỂM TRA ĐỘ HOÀN CHỈNH
						received_size = sum(len(data) for data in self.reassembly_buffer.values())

						if received_size == self.expected_frame_size:
							# THÀNH CÔNG: Ghép các mảnh lại
							full_frame_data = bytearray()
							
							# Sắp xếp các mảnh theo đúng thứ tự offset
							sorted_offsets = sorted(self.reassembly_buffer.keys())
							
							for o in sorted_offsets:
								full_frame_data.extend(self.reassembly_buffer[o])

							# Cập nhật hình ảnh
							self.updateMovie(self.writeFrame(full_frame_data))
						else:
							# THẤT BẠI: Hủy bỏ khung hình này
							print(f"Discarding corrupt frame {self.frameNbr}. "
								  f"Received {received_size} / {self.expected_frame_size} bytes.")

						# Xóa bộ đệm cho khung hình tiếp theo
						self.reassembly_buffer = {}
						self.expected_frame_size = 0
			
			except:
				# Xử lý khi dừng hoặc teardown
				if self.playEvent.isSet(): 
					break
				
				if self.teardownAcked == 1:
					self.rtpSocket.shutdown(socket.SHUT_RDWR)
					self.rtpSocket.close()
					break
					
	def writeFrame(self, data):
		cachename = CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT
		file = open(cachename, "wb")
		file.write(data)
		file.close()
		return cachename
	
	def updateMovie(self, imageFile):
		"""SỬA ĐỔI HOÀN TOÀN: Tự động resize ảnh để vừa cửa sổ."""
		try:
			# 1. Mở ảnh gốc
			img = Image.open(imageFile)
			
			# 2. Lấy kích thước gốc
			original_width, original_height = img.size
			
			# 3. Tính toán kích thước mới dựa trên MAX_DISPLAY_WIDTH
			if original_width > self.MAX_DISPLAY_WIDTH:
				# Tính tỷ lệ co lại
				w_percent = (self.MAX_DISPLAY_WIDTH / float(original_width))
				# Tính chiều cao mới theo tỷ lệ
				new_height = int((float(original_height) * float(w_percent)))
				new_size = (self.MAX_DISPLAY_WIDTH, new_height)
			else:
				# Nếu ảnh đã nhỏ hơn max width, giữ nguyên
				new_size = (original_width, original_height)
			
			# 4. Resize ảnh với bộ lọc chất lượng cao (LANCZOS)
			resized_img = img.resize(new_size, Image.LANCZOS) 
			
			# 5. Tạo PhotoImage từ ảnh ĐÃ RESIZE
			photo = ImageTk.PhotoImage(resized_img)
			
			# 6. Cập nhật label (Xóa 'height=288')
			self.label.configure(image = photo) 
			self.label.image = photo
			
		except Exception as e:
			print(f"Error updating movie: {e}")
		
	def connectToServer(self):
		self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		try:
			self.rtspSocket.connect((self.serverAddr, self.serverPort))
		except:
			tkinter.messagebox.showwarning('Connection Failed', 'Connection to \'%s\' failed.' %self.serverAddr)
	
	def sendRtspRequest(self, requestCode):
		# ... (phần logic if/elif giữ nguyên) ...
		if requestCode == self.SETUP and self.state == self.INIT:
			threading.Thread(target=self.recvRtspReply).start()
			self.rtspSeq += 1
			request = f"SETUP {self.fileName} RTSP/1.0\n"   
			request += f"CSeq: {self.rtspSeq}\n"             
			request += f"Transport: RTP/UDP; client_port= {self.rtpPort}\n"  
			self.requestSent = self.SETUP

		elif requestCode == self.PLAY and self.state == self.READY:
			self.rtspSeq += 1
			request = f"PLAY {self.fileName} RTSP/1.0\n"    
			request += f"CSeq: {self.rtspSeq}\n"             
			request += f"Session: {self.sessionId}\n"        
			self.requestSent = self.PLAY
		
		elif requestCode == self.PAUSE and self.state == self.PLAYING:
			self.rtspSeq += 1
			request = f"PAUSE {self.fileName} RTSP/1.0\n"   
			request += f"CSeq: {self.rtspSeq}\n"             
			request += f"Session: {self.sessionId}\n"        
			self.requestSent = self.PAUSE
			
		elif requestCode == self.TEARDOWN and not self.state == self.INIT:
			self.rtspSeq += 1
			request = f"TEARDOWN {self.fileName} RTSP/1.0\n"  
			request += f"CSeq: {self.rtspSeq}\n"              
			request += f"Session: {self.sessionId}\n"        
			self.requestSent = self.TEARDOWN
		else:
			return
		
		self.rtspSocket.send(request.encode())
		print('\nData sent:\n' + request)
	
	def recvRtspReply(self):
		while True:
			try:
				reply = self.rtspSocket.recv(1024)
				if reply: 
					print("Data received:\n" + reply.decode("utf-8"))
					self.parseRtspReply(reply.decode("utf-8"))
				
				if self.requestSent == self.TEARDOWN:
					self.rtspSocket.shutdown(socket.SHUT_RDWR)
					self.rtspSocket.close()
					break
			except:
				break
	
	def parseRtspReply(self, data):
		lines = data.split('\n')
		seqNum = int(lines[1].split(' ')[1])
		
		if seqNum == self.rtspSeq:
			sessionLine = lines[2].split(' ')
			if self.sessionId == 0:
				self.sessionId = int(sessionLine[1])
			
			if self.sessionId == int(sessionLine[1]):
				statusCode = int(lines[0].split(' ')[1])
				if statusCode == 200: 
					if self.requestSent == self.SETUP:
						self.state = self.READY
						self.openRtpPort() 
						# Update state SETUP
						self.setup["state"] = "disabled"
						self.start["state"] = "normal"
						self.pause["state"] = "disabled"
						self.teardown["state"] = "normal"
      
					elif self.requestSent == self.PLAY:
						self.state = self.PLAYING
						# Update state PLAY
						self.setup["state"] = "disabled"
						self.start["state"] = "disabled"
						self.pause["state"] = "normal"
						self.teardown["state"] = "normal"
      
					elif self.requestSent == self.PAUSE:
						self.state = self.READY
						self.playEvent.set()
						# Update state PLAY
						self.setup["state"] = "disabled"
						self.start["state"] = "normal"
						self.pause["state"] = "disabled"
						self.teardown["state"] = "normal"
      
					elif self.requestSent == self.TEARDOWN:
						self.state = self.INIT
						self.teardownAcked = 1
	
	def openRtpPort(self):
		self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		self.rtpSocket.settimeout(0.5)
		try:
			self.rtpSocket.bind(("", self.rtpPort))
		except:
			tkinter.messagebox.showwarning('Unable to Bind', 'Unable to bind PORT=%d' %self.rtpPort)

	def handler(self):
		self.pauseMovie()
		if tkinter.messagebox.askokcancel("Quit?", "Are you sure you want to quit?"):
			self.exitClient()
		else: 
			self.playMovie()
	def update_timer(self):
		"""Cập nhật bộ đếm thời gian mỗi giây."""
		if self.timer_running:
			self.elapsed_time += 1
			
			minutes = (self.elapsed_time % 3600) // 60
			seconds = self.elapsed_time % 60
			time_string = f"{minutes:02}:{seconds:02}"
			
			self.timer_label.config(text=time_string)
			
			self.master.after(1000, self.update_timer)