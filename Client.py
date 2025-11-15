from tkinter import *
import tkinter.messagebox
from PIL import Image, ImageTk
import socket, threading, sys, traceback, os
import time

from RtpPacket import RtpPacket

CACHE_FILE_NAME = "cache-"
CACHE_FILE_EXT = ".jpg"

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
		
		# Jitter Buffer (cho các frame hoàn chỉnh)
		self.jitterBuffer = {}
		self.playoutCounter = 0
		self.isPreBuffered = False

		self.PRE_BUFFER_SIZE = 40
		self.FRAME_PERIOD = 0.05
		
		# THÊM MỚI: Hai sự kiện điều khiển riêng biệt
		self.rtpListenEvent = None    # Dùng để dừng luồng listenRtp (chỉ khi teardown)
		self.playoutEvent = None      # Dùng để TẠM DỪNG luồng playFromBuffer
		self.playoutThread = None     # Tham chiếu đến luồng playout
		
	def createWidgets(self):
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

		# THAY ĐỔI: Dừng cả hai luồng
		if self.rtpListenEvent:
			self.rtpListenEvent.set()
		if self.playoutEvent:
			self.playoutEvent.set()

		if os.path.exists(CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT):
			os.remove(CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT)
		
		self.master.destroy()

	def pauseMovie(self):
		if self.state == self.PLAYING:
			self.sendRtspRequest(self.PAUSE)
   
			self.timer_running = False
			
			# THAY ĐỔI: Chỉ dừng luồng playout
			if self.playoutEvent:
				self.playoutEvent.set() # set() = "pause"
	
	def playMovie(self):
		if self.state == self.READY:
			
			if self.playoutThread is None or not self.playoutThread.is_alive():
				self.playoutThread = threading.Thread(target=self.playFromBuffer)
				self.playoutThread.start()
			
			# Gửi yêu cầu PLAY
			self.sendRtspRequest(self.PLAY)
			
			# Báo cho luồng playout tiếp tục (clear() = "run")
			if self.playoutEvent:
				self.playoutEvent.clear()
			
			self.timer_running = True
			self.update_timer()
	
	def listenRtp(self):
		# print("Listen thread started (buffering in background).")
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
						# print(f"Runt packet received for frame {currFrameNbr}. Discarding.")
						continue 

					offset = int.from_bytes(payload[0:4], byteorder='big')
					total_size = int.from_bytes(payload[4:8], byteorder='big')
					fragment_data = payload[JPEG_HEADER_SIZE:]

					# Logic tái hợp
					if currFrameNbr > self.frameNbr:
						self.frameNbr = currFrameNbr
						self.reassembly_buffer = {} 
						self.expected_frame_size = total_size 
						self.reassembly_buffer[offset] = fragment_data
					
					elif currFrameNbr == self.frameNbr:
						if self.expected_frame_size > 0:
							self.reassembly_buffer[offset] = fragment_data

					# Nếu đây là mảnh cuối cùng (M=1) và bộ đệm có dữ liệu
					if marker == 1 and self.reassembly_buffer:
						received_size = sum(len(data) for data in self.reassembly_buffer.values())

						if received_size == self.expected_frame_size:
							full_frame_data = bytearray()
							sorted_offsets = sorted(self.reassembly_buffer.keys())
							for o in sorted_offsets:
								full_frame_data.extend(self.reassembly_buffer[o])

							# Thêm vào Jitter Buffer
							self.jitterBuffer[self.frameNbr] = full_frame_data

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
		
		# print("Listen thread stopped.")

	def playFromBuffer(self):
		while not self.rtpListenEvent.is_set(): 
			try:
				if self.playoutEvent.is_set():
					time.sleep(0.05)
					continue

				# 1. Xử lý Pre-buffering (chỉ chạy lần đầu hoặc khi re-buffer)
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
						time.sleep(0.05) 
						continue

				# 2. Logic Playout (Khi đã pre-buffer xong và không PAUSE)
				if self.playoutCounter in self.jitterBuffer:
					frameData = self.jitterBuffer.pop(self.playoutCounter)
					
					try:
						self.updateMovie(self.writeFrame(frameData))
					except TclError:
						print("GUI window closed. Stopping playout thread.")
						break 
					
					self.playoutCounter += 1
					time.sleep(self.FRAME_PERIOD) 
				
				else:
					# Bị mất frame hoặc jitter
					if not self.jitterBuffer and self.isPreBuffered:
						print("Jitter buffer empty. Re-buffering...")
						self.isPreBuffered = False
						time.sleep(0.05)
					
					elif self.jitterBuffer:
						available_frames = sorted(self.jitterBuffer.keys())
						next_available = -1
						for f_num in available_frames:
							if f_num > self.playoutCounter:
								next_available = f_num
								break
						
						if next_available != -1:
							print(f"Skipping frame {self.playoutCounter}. Jumping to {next_available}")
							self.playoutCounter = next_available
						else:
							time.sleep(0.01)
					else:
						time.sleep(0.01) 
			
			except Exception as e:
				if not self.rtpListenEvent.is_set():
					print(f"Error in playout thread: {e}")
					traceback.print_exc(file=sys.stdout)
					time.sleep(0.05)

	def writeFrame(self, data):
		cachename = CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT
		try:
			file = open(cachename, "wb")
			file.write(data)
			file.close()
			return cachename
		except:
			print("Error writing cache file")
			return None
	
	def updateMovie(self, imageFile):
		if imageFile is None:
			return
   
		try:
			img = Image.open(imageFile)
			original_width, original_height = img.size
			
			if original_width > self.MAX_DISPLAY_WIDTH:
				w_percent = (self.MAX_DISPLAY_WIDTH / float(original_width))
				new_height = int((float(original_height) * float(w_percent)))
				new_size = (self.MAX_DISPLAY_WIDTH, new_height)
			else:
				new_size = (original_width, original_height)
			
			resized_img = img.resize(new_size, Image.LANCZOS) 
			photo = ImageTk.PhotoImage(resized_img)
			
			self.label.configure(image = photo) 
			self.label.image = photo
			
		except Exception as e:
			print(f"Error updating movie: {e}. Image file: {imageFile}")
			pass
		
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
						self.rtpListenEvent.clear() # clear() = "run"
						
						self.playoutEvent = threading.Event()
						self.playoutEvent.set()
						
						# Bắt đầu luồng nghe ngay lập tức
						threading.Thread(target=self.listenRtp).start()
						
						# Cập nhật GUI
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
						# XÓA: Không cần set event ở đây, 
						#      nó đã được xử lý trong `pauseMovie()`
						self.setup["state"] = "disabled"
						self.start["state"] = "normal"
						self.pause["state"] = "disabled"
						self.teardown["state"] = "normal"
      
					elif self.requestSent == self.TEARDOWN:
						self.state = self.INIT
						# THAY ĐỔI: Dừng cả hai luồng
						if self.rtpListenEvent:
							self.rtpListenEvent.set()
						if self.playoutEvent:
							self.playoutEvent.set()
						self.teardownAcked = 1
	
	def openRtpPort(self):
		self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
		# Đặt timeout
		self.rtpSocket.settimeout(0.05) 
  
		try:
			buffer_size = 2097152
			self.rtpSocket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, buffer_size)
			print(f"Set SO_RCVBUF to {buffer_size} bytes")
		except Exception as e:
			print(f"Warning: Could not set SO_RCVBUF. Using default. Error: {e}")
  
		try:
			self.rtpSocket.bind(("", self.rtpPort))
			print(f"RTP port opened at {self.rtpPort}")
		except:
			tkinter.messagebox.showwarning('Unable to Bind', 'Unable to bind PORT=%d' %self.rtpPort)

	def handler(self):
		self.pauseMovie()
		if tkinter.messagebox.askokcancel("Quit?", "Are you sure you want to quit?"):
			self.exitClient()
		else: 
			self.playMovie()
   
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