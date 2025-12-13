from tkinter import *
import tkinter.messagebox
from PIL import Image, ImageTk
import socket, threading, sys, traceback
import time
import io 

from RtpPacket import RtpPacket

JPEG_HEADER_SIZE = 8

class Client:
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
  
		# Bộ đệm tái hợp (cho các mảnh của 1 frame)
		self.reassembly_buffer = {}
		self.expected_frame_size = 0
		
		# Jitter Buffer
		self.jitterBuffer = {}
		self.playoutCounter = 0
		self.isPreBuffered = False

		# --- CẤU HÌNH BUFFER NÂNG CAO ---
		# Số frame tải trước khi bắt đầu chạy
		self.PRE_BUFFER_SIZE = 50
		# Số frame tối đa tải ngầm khi PAUSE
		self.MAX_BUFFER_SIZE = 100 
		
		self.FRAME_PERIOD = 1.0/30
		
		self.rtpListenEvent = None    
		self.playoutEvent = None      
		self.playoutThread = None     
		
	def createWidgets(self):
        # --- 1. CẤU HÌNH LAYOUT (Grid Weights) ---
        # Điều này đảm bảo row 0 (video) giãn ra, đẩy row 1, 2 xuống đáy
		self.master.grid_rowconfigure(0, weight=1) 
		self.master.grid_columnconfigure(0, weight=1)
		self.master.grid_columnconfigure(1, weight=1)
		self.master.grid_columnconfigure(2, weight=1)
		self.master.grid_columnconfigure(3, weight=1)	
        # --- 2. TẠO PLACEHOLDER (Màn hình chờ) ---
        # Kích thước mặc định cho khung video (ví dụ 640x480 hoặc tỷ lệ 16:9)
		self.placeholder_w = 600
		self.placeholder_h = 400
        
        # Tạo ảnh nền đen
		bg_image = Image.new('RGB', (self.placeholder_w, self.placeholder_h), color='#f0f0f0')
		self.photo = ImageTk.PhotoImage(bg_image)

        # Label hiển thị video (Bắt đầu bằng placeholder đen)
		self.label = Label(self.master, image=self.photo, bg="#f0f0f0")
		self.label.image = self.photo # Giữ tham chiếu để không bị Garbage Collection xóa
		self.label.grid(row=0, column=0, columnspan=4, sticky=W+E+N+S, padx=5, pady=5)

        # --- 3. TIMER LABEL ---
		self.timer_label = Label(self.master, text="00:00", font=("Helvetica", 14, "bold"), fg="#333")
		self.timer_label.grid(row=1, column=0, columnspan=4, padx=5, pady=5)

        # --- 4. CÁC NÚT BẤM (BUTTONS) ---
        # Tạo style padding chung cho đẹp
		btn_padding_x = 10
		btn_padding_y = 5
		btn_width = 15

		self.setup = Button(self.master, width=btn_width, padx=btn_padding_x, pady=btn_padding_y)
		self.setup["text"] = "Setup"
		self.setup["command"] = self.setupMovie
		self.setup.grid(row=2, column=0, padx=2, pady=10)
        
		self.start = Button(self.master, width=btn_width, padx=btn_padding_x, pady=btn_padding_y)
		self.start["text"] = "Play"
		self.start["command"] = self.playMovie
		self.start.grid(row=2, column=1, padx=2, pady=10)
        
		self.pause = Button(self.master, width=btn_width, padx=btn_padding_x, pady=btn_padding_y)
		self.pause["text"] = "Pause"
		self.pause["command"] = self.pauseMovie
		self.pause.grid(row=2, column=2, padx=2, pady=10)
        
		self.teardown = Button(self.master, width=btn_width, padx=btn_padding_x, pady=btn_padding_y)
		self.teardown["text"] = "Teardown"
		self.teardown["command"] =  self.exitClient
		self.teardown.grid(row=2, column=3, padx=2, pady=10)

		# Set trạng thái ban đầu
		self.setup["state"] = "normal"
		self.start["state"] = "disabled"
		self.pause["state"] = "disabled"
		self.teardown["state"] = "disabled"
	
	def setupMovie(self):
		if self.state == self.INIT:
			self.sendRtspRequest(self.SETUP)
			self.setup["state"] = "disable"
	
	def exitClient(self):
		self.sendRtspRequest(self.TEARDOWN) 	
		self.timer_running = False
		self.elapsed_time = 0
		try:
			self.timer_label.config(text="00:00")
		except TclError:
			pass 

		if self.rtpListenEvent:
			self.rtpListenEvent.set()
		if self.playoutEvent:
			self.playoutEvent.set()
		
		self.master.destroy()

	def pauseMovie(self):
		"""
		SỬA ĐỔI NÂNG CAO:
		- Khi bấm Pause: Dừng hiển thị ảnh, dừng đồng hồ.
		- NHƯNG: Không gửi RTSP PAUSE ngay. Để Buffer tự lấp đầy ngầm.
		"""
		if self.state == self.PLAYING:
			# 1. Dừng đồng hồ
			self.timer_running = False
			
			# 2. Dừng hiển thị ảnh (set event để playFromBuffer tạm dừng loop)
			if self.playoutEvent:
				self.playoutEvent.set() 
			
			# 3. Cập nhật nút bấm thủ công (Giả lập trạng thái Pause cho người dùng)
			# Ta phải mở nút Play để người dùng bấm tiếp được
			self.start["state"] = "normal"
			self.pause["state"] = "disabled"
			
			# LƯU Ý: KHÔNG gửi self.sendRtspRequest(self.PAUSE) ở đây!
			# Việc gửi lệnh này sẽ do listenRtp quyết định khi buffer đầy.
			# print("\n[Smart Cache] UI Paused. Buffering in background until full...")

	def playMovie(self):
		"""
		SỬA ĐỔI NÂNG CAO:
		- Xử lý 2 trường hợp:
		  1. Server vẫn đang gửi (do buffer chưa đầy): Chỉ cần Resume hiển thị.
		  2. Server đã dừng (do buffer đã đầy và tự gửi Pause): Gửi lệnh RTSP PLAY.
		"""
		# Đảm bảo luồng playout đang chạy
		if self.playoutThread is None or not self.playoutThread.is_alive():
			self.playoutThread = threading.Thread(target=self.playFromBuffer)
			self.playoutThread.start()
		
		# Resume hiển thị ảnh
		if self.playoutEvent:
			self.playoutEvent.clear()
		
		self.timer_running = True
		self.update_timer()

		# Cập nhật nút bấm
		self.start["state"] = "disabled"
		self.pause["state"] = "normal"

		# LOGIC QUYẾT ĐỊNH GỬI LỆNH
		if self.state == self.READY:
			# Trường hợp: Buffer đã đầy, listenRtp đã tự gửi PAUSE -> State là READY
			# Cần gửi PLAY để Server bắn tiếp
			# print("[Smart Cache] Resuming from READY state. Sending RTSP PLAY.")
			self.sendRtspRequest(self.PLAY)

	def listenRtp(self):
		while True:
			try:
				if self.rtpListenEvent.is_set(): 
					break

				data = self.rtpSocket.recv(20480)
				if data:
					rtpPacket = RtpPacket()
					rtpPacket.decode(data)
					
					currFrameNbr = rtpPacket.seqNum()
					marker = rtpPacket.marker()
					payload = rtpPacket.getPayload()

					if len(payload) < JPEG_HEADER_SIZE:
						continue 

					offset = int.from_bytes(payload[0:4], byteorder='big')
					total_size = int.from_bytes(payload[4:8], byteorder='big')
					fragment_data = payload[JPEG_HEADER_SIZE:]

					# Logic tái hợp (Reassembly)
					if currFrameNbr > self.frameNbr:
						self.frameNbr = currFrameNbr
						self.reassembly_buffer = {} 
						self.expected_frame_size = total_size 
						self.reassembly_buffer[offset] = fragment_data
					
					elif currFrameNbr == self.frameNbr:
						if self.expected_frame_size > 0:
							self.reassembly_buffer[offset] = fragment_data

					# Khi nhận đủ frame
					if marker == 1 and self.reassembly_buffer:
						received_size = sum(len(d) for d in self.reassembly_buffer.values())

						if received_size == self.expected_frame_size:
							full_frame_data = bytearray()
							sorted_offsets = sorted(self.reassembly_buffer.keys())
							for o in sorted_offsets:
								full_frame_data.extend(self.reassembly_buffer[o])

							# Thêm vào Jitter Buffer
							self.jitterBuffer[self.frameNbr] = full_frame_data
							
							# --- LOGIC THÔNG MINH (SMART BUFFERING) ---
							# Nếu người dùng đã bấm Pause (playoutEvent is set) 
							# VÀ Buffer đã đạt ngưỡng tối đa -> Gửi lệnh PAUSE thật
							if self.playoutEvent.is_set() and len(self.jitterBuffer) >= self.MAX_BUFFER_SIZE:
								if self.state == self.PLAYING and self.requestSent != self.PAUSE:
									# print(f"[Smart Cache] Buffer full ({len(self.jitterBuffer)} frames). Sending real RTSP PAUSE now.")
									# Gửi lệnh PAUSE lên Server để tiết kiệm băng thông
									self.sendRtspRequest(self.PAUSE)

						self.reassembly_buffer = {}
						self.expected_frame_size = 0
			
			except socket.timeout:
				if self.rtpListenEvent.is_set():
					break
				continue
   
			except Exception as e:
				if not self.rtpListenEvent.is_set():
					print(f"Error in listenRtp: {e}")
				if self.teardownAcked == 1:
					break
		
		if self.teardownAcked == 1:
			try:
				self.rtpSocket.shutdown(socket.SHUT_RDWR)
				self.rtpSocket.close()
			except:
				pass

	def playFromBuffer(self):
		FPS = 30
		self.FRAME_PERIOD = 1.0 / FPS
		LOW_BUFFER_THRESHOLD = 20
  
		while not self.rtpListenEvent.is_set(): 
			start_time = time.time()
			try:
				# Nếu đang Pause, ngủ một chút để tiết kiệm CPU
				if self.playoutEvent.is_set():
					time.sleep(0.05)
					continue

				# --- LOGIC AUTO-RESUME ---
                # Nếu số lượng frame còn lại thấp VÀ Server đang nghỉ (READY) thì gửi lệnh PLAY để Server bơm thêm dữ liệu
				if len(self.jitterBuffer) < LOW_BUFFER_THRESHOLD and self.state == self.READY:
                    # Kiểm tra thêm để tránh spam lệnh PLAY liên tục nếu đang đợi phản hồi
					if self.requestSent != self.PLAY:
                        # print("[Smart Cache] Buffer running low. Resuming stream...")
						self.sendRtspRequest(self.PLAY)

				# 1. Pre-buffering (chỉ chạy khi bắt đầu hoặc cạn buffer)
				if not self.isPreBuffered:
					if len(self.jitterBuffer) >= self.PRE_BUFFER_SIZE:
						self.isPreBuffered = True
						if self.jitterBuffer:
							self.playoutCounter = min(self.jitterBuffer.keys())
						else:
							self.isPreBuffered = False
							time.sleep(0.05)
							continue
					else:
						# Đang buffer...
						time.sleep(0.05) 
						continue

				# 2. Logic Playout
				if self.playoutCounter in self.jitterBuffer:
					frameData = self.jitterBuffer.pop(self.playoutCounter)
                
                # Cập nhật ảnh (Decode + Resize + Display)
                # Đây là tác vụ nặng nhất!
					try:
						self.updateMovie(frameData)
					except TclError:
						break 
					
					self.playoutCounter += 1
					
					# --- TÍNH TOÁN THỜI GIAN NGỦ THÔNG MINH ---
					# Tính thời gian đã tiêu tốn cho việc xử lý ảnh
					process_duration = time.time() - start_time
					
					# Thời gian cần ngủ = Chu kỳ chuẩn - Thời gian đã mất
					sleep_time = self.FRAME_PERIOD - process_duration
					
					if sleep_time > 0:
						time.sleep(sleep_time)
					else:
						# Nếu xử lý quá chậm (máy lag), không ngủ, chạy tiếp ngay
						pass
				
				else:
					# Xử lý mất gói hoặc trễ
					if not self.jitterBuffer and self.isPreBuffered:
						# print("Buffering (Buffer Empty)...")
						self.isPreBuffered = False
						time.sleep(0.05)
					elif self.jitterBuffer:
						# Skip frame logic
						available_frames = sorted(self.jitterBuffer.keys())
						next_available = -1
						for f_num in available_frames:
							if f_num > self.playoutCounter:
								next_available = f_num
								break
						if next_available != -1:
							self.playoutCounter = next_available
						else:
							time.sleep(0.01)
					else:
						time.sleep(0.01) 
			
			except Exception as e:
				if not self.rtpListenEvent.is_set():
					print(f"Error in playout: {e}")
					time.sleep(0.05)

	def updateMovie(self, imageBytes):
		"""Cập nhật hình ảnh - Thread Safe"""
		if imageBytes is None:
			return
   
		try:
			stream = io.BytesIO(imageBytes)
			img = Image.open(stream)
			
			original_width, original_height = img.size
			
			if original_width > self.MAX_DISPLAY_WIDTH:
				w_percent = (self.MAX_DISPLAY_WIDTH / float(original_width))
				new_height = int((float(original_height) * float(w_percent)))
				new_size = (self.MAX_DISPLAY_WIDTH, new_height)
			else:
				new_size = (original_width, original_height)
			
			resized_img = img.resize(new_size, Image.LANCZOS) 
			photo = ImageTk.PhotoImage(resized_img)
			
			self.master.after(0, lambda: self._update_label(photo))
			
		except Exception as e:
			print(f"Error updating movie: {e}")
			pass
	
	def _update_label(self, photo):
		try:
			self.label.configure(image = photo) 
			self.label.image = photo
		except TclError:
			pass
		
	def connectToServer(self):
		self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		try:
			self.rtspSocket.connect((self.serverAddr, self.serverPort))
		except:
			tkinter.messagebox.showwarning('Connection Failed', 'Connection to \'%s\' failed.' %self.serverAddr)
	
	def sendRtspRequest(self, requestCode):
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
					if self.rtpListenEvent:
						self.rtpListenEvent.set()
					if self.playoutEvent:
						self.playoutEvent.set()

					self.rtspSocket.shutdown(socket.SHUT_RDWR)
					self.rtspSocket.close()
					break
			except:
				print("RTSP connection closed.")
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
						
						self.rtpListenEvent = threading.Event()
						self.rtpListenEvent.clear() 
						
						self.playoutEvent = threading.Event()
						self.playoutEvent.set()
						
						threading.Thread(target=self.listenRtp).start()
						
						self.setup["state"] = "disabled"
						self.start["state"] = "normal"
						self.pause["state"] = "disabled"
						self.teardown["state"] = "normal"
      
					elif self.requestSent == self.PLAY:
						self.state = self.PLAYING
						self.setup["state"] = "disabled"
						self.start["state"] = "disabled"
						self.pause["state"] = "normal"
						self.teardown["state"] = "normal"
      
					elif self.requestSent == self.PAUSE:
						self.state = self.READY
						# Nút đã được xử lý manual ở pauseMovie, 
						# nhưng cập nhật lại ở đây để đồng bộ chính xác.
						self.setup["state"] = "disabled"
						self.start["state"] = "normal"
						self.pause["state"] = "disabled"
						self.teardown["state"] = "normal"
      
					elif self.requestSent == self.TEARDOWN:
						self.state = self.INIT
						if self.rtpListenEvent:
							self.rtpListenEvent.set()
						if self.playoutEvent:
							self.playoutEvent.set()
						self.teardownAcked = 1
	
	def openRtpPort(self):
		self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		self.rtpSocket.settimeout(0.05) 
		try:
			buffer_size = 2097152 
			self.rtpSocket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, buffer_size)
		except Exception as e:
			print(f"Warning: Could not set SO_RCVBUF. Error: {e}")
  
		try:
			self.rtpSocket.bind(("", self.rtpPort))
		except:
			tkinter.messagebox.showwarning('Unable to Bind', 'Unable to bind PORT=%d' %self.rtpPort)

	def handler(self):
		self.pauseMovie()
		if tkinter.messagebox.askokcancel("Quit?", "Are you sure you want to quit?"):
			self.exitClient()
   
	def update_timer(self):
		if self.timer_running:
			self.elapsed_time += 1
			minutes = (self.elapsed_time % 3600) // 60
			seconds = self.elapsed_time % 60
			time_string = f"{minutes:02}:{seconds:02}"
			try:
				self.timer_label.config(text=time_string)
				self.master.after(1000, self.update_timer)
			except TclError:
				pass