import socket
import time
import math
import csv 
import numpy as np
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
from datetime import datetime

# --- VNA İLETİŞİM KATMANI ---

class VNAClient:
    def __init__(self, log_callback=None):
        self.sock = None
        self.ip = ""
        self.port = 0
        self.log_callback = log_callback

    def log(self, msg, type="INFO"):
        if self.log_callback:
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.log_callback(f"[{timestamp}] [{type}] {msg}")
        else:
            print(f"[{type}] {msg}")

    def connect(self, ip, port):
        try:
            if self.sock:
                self.sock.close()
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(5.0) 
            self.sock.connect((ip, int(port)))
            self.ip = ip
            self.port = port
            self.log(f"Bağlantı başarılı: {ip}:{port}", "OK")
            return True
        except Exception as e:
            self.log(f"Bağlantı Hatası: {e}", "ERROR")
            return False

    def disconnect(self):
        if self.sock:
            try:
                self.sock.close()
                self.log("Bağlantı kapatıldı.", "INFO")
            except:
                pass
            self.sock = None

    def send_cmd(self, cmd):
        if not self.sock: return
        try:
            # SCPI komutları genellikle newline ile biter
            self.sock.sendall((cmd + "\n").encode('ascii'))
        except Exception as e:
            self.log(f"Gönderme hatası ({cmd}): {e}", "ERROR")

    def query(self, cmd):
        self.send_cmd(cmd)
        try:
            response = ""
            while True:
                try:
                    # Büyük veri paketleri için parça parça okuma
                    part = self.sock.recv(4096).decode('ascii')
                    response += part
                    # Dokümana göre cevaplar newline ile biter
                    if response.endswith("\n"):
                        break
                except socket.timeout:
                    self.log(f"Sorgu zaman aşımı: {cmd}", "WARN")
                    break
                except Exception as e:
                    self.log(f"Okuma hatası: {e}", "ERROR")
                    break
            return response.strip()
        except:
            return None

    def get_trace_data(self, param):
        # PDF Referans 4.3.23: VNA:TRACE:DATA?
        raw = self.query(f":VNA:TRAC:DATA? {param}")
        if not raw: return []
        
        try:
            # Dokümana göre veri formatı: [freq, real, imag], [freq, real, imag]...
            clean_str = raw.replace('[', '').replace(']', '')
            values = clean_str.split(',')
            parsed = []
            
            # Veri formatı: Freq, Real, Imag (3'lü gruplar halinde)
            for i in range(0, len(values), 3):
                if i+2 < len(values):
                    freq = float(values[i])
                    real = float(values[i+1])
                    imag = float(values[i+2])
                    parsed.append({'freq': freq, 'val': complex(real, imag)})
            return parsed
        except Exception as e:
            self.log(f"Veri işleme hatası ({param}): {e}", "ERROR")
            return []

# --- YARDIMCI FONKSİYONLAR ---

def parse_frequency(value_str):
    """
    Kullanıcı girdisini (örn: '100 kHz') Hz cinsinden integer'a çevirir.
    """
    s = value_str.strip().lower()
    multiplier = 1.0
    if s.endswith('ghz'): multiplier, s = 1e9, s.replace('ghz', '')
    elif s.endswith('mhz'): multiplier, s = 1e6, s.replace('mhz', '')
    elif s.endswith('khz'): multiplier, s = 1e3, s.replace('khz', '')
    elif s.endswith('hz'): multiplier, s = 1.0, s.replace('hz', '')
    try:
        return int(float(s) * multiplier)
    except:
        return None

# --- ARAYÜZ SINIFI ---

class VNAApp:
    def __init__(self, root):
        self.root = root
        self.root.title("LibreVNA Kontrol & CSV Görüntüleyici v3")
        self.root.geometry("1200x950")
        
        # Konsol Loglama Fonksiyonu
        self.client = VNAClient(log_callback=self.add_log)
        self.is_streaming = False
        
        self.current_settings = {"start": 0, "stop": 0} 
        self.latest_data = {} # CSV kaydı ve çizim için veri deposu

        self.lines = {'S11': None, 'S12': None, 'S21': None, 'S22': None}
        
        self.setup_ui()
        self.init_plots()

    def setup_ui(self):
        # Ana çerçeveler
        main_pane = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        main_pane.pack(fill=tk.BOTH, expand=True)

        top_frame = ttk.Frame(main_pane)
        main_pane.add(top_frame, weight=4) 

        # -- Sol Panel (Kontrol) --
        control_frame = ttk.Frame(top_frame, padding="10", width=260)
        control_frame.pack(side=tk.LEFT, fill=tk.Y)
        
        # 1. Bağlantı Bölümü
        ttk.Label(control_frame, text="BAĞLANTI", font=("Arial", 10, "bold")).pack(pady=(0,5))
        self.entry_ip = ttk.Entry(control_frame); self.entry_ip.insert(0, "192.168.1.107"); self.entry_ip.pack(fill=tk.X)
        self.entry_port = ttk.Entry(control_frame); self.entry_port.insert(0, "19542"); self.entry_port.pack(fill=tk.X, pady=(5,15))
        
        # 2. Tarama Ayarları
        ttk.Label(control_frame, text="TARAMA AYARLARI", font=("Arial", 10, "bold")).pack(pady=(0,5))
        
        frm_freq = ttk.Frame(control_frame)
        frm_freq.pack(fill=tk.X)
        
        ttk.Label(frm_freq, text="Başlangıç:").grid(row=0, column=0, sticky="w")
        self.entry_start = ttk.Entry(frm_freq, width=15); self.entry_start.insert(0, "100 kHz"); self.entry_start.grid(row=0, column=1, padx=5)
        
        ttk.Label(frm_freq, text="Bitiş:").grid(row=1, column=0, sticky="w")
        self.entry_stop = ttk.Entry(frm_freq, width=15); self.entry_stop.insert(0, "6 GHz"); self.entry_stop.grid(row=1, column=1, padx=5)
        
        ttk.Label(frm_freq, text="Nokta Sayısı:").grid(row=2, column=0, sticky="w")
        self.entry_points = ttk.Entry(frm_freq, width=15); self.entry_points.insert(0, "201"); self.entry_points.grid(row=2, column=1, padx=5)
        
        # 3. Ölçüm Konfigürasyonu (GÜNCELLENDİ)
        ttk.Label(control_frame, text="ÖLÇÜM KONFİGÜRASYONU", font=("Arial", 10, "bold")).pack(pady=(15,5))
        
        frm_cfg = ttk.Frame(control_frame)
        frm_cfg.pack(fill=tk.X)
        
        # IF Bandwidth (IFBW)
        ttk.Label(frm_cfg, text="IF Bant Genişliği:").grid(row=0, column=0, sticky="w")
        self.combo_ifbw = ttk.Combobox(frm_cfg, values=["10 Hz", "100 Hz", "1 kHz", "10 kHz", "50 kHz"], width=13)
        self.combo_ifbw.current(2) # Varsayılan 1 kHz
        self.combo_ifbw.grid(row=0, column=1, padx=5, pady=2)

        # Averaging (Ortalama)
        ttk.Label(frm_cfg, text="Ortalama (Avg):").grid(row=1, column=0, sticky="w")
        self.entry_avg = ttk.Entry(frm_cfg, width=15)
        self.entry_avg.insert(0, "1")
        self.entry_avg.grid(row=1, column=1, padx=5, pady=2)
        
        # --- YENİ EKLENEN ÖZELLİKLER ---
        
        # Tarama Tipi (Sweep Type) - LIN / LOG
        ttk.Label(frm_cfg, text="Tarama Tipi:").grid(row=2, column=0, sticky="w")
        self.combo_sweep = ttk.Combobox(frm_cfg, values=["LIN", "LOG"], width=13)
        self.combo_sweep.current(0) # Varsayılan LIN
        self.combo_sweep.grid(row=2, column=1, padx=5, pady=2)

        # Çıkış Gücü (Stimulus Power) - dBm
        ttk.Label(frm_cfg, text="Güç (dBm):").grid(row=3, column=0, sticky="w")
        self.entry_power = ttk.Entry(frm_cfg, width=15)
        self.entry_power.insert(0, "0") # Varsayılan 0 dBm
        self.entry_power.grid(row=3, column=1, padx=5, pady=2)
        
        # -------------------------------
        
        # 4. Akış Kontrolü
        ttk.Separator(control_frame, orient='horizontal').pack(fill='x', pady=15)
        self.btn_stream = tk.Button(control_frame, text="CANLI AKIŞI BAŞLAT", bg="#4CAF50", fg="white", font=("Arial", 11, "bold"), command=self.toggle_streaming)
        self.btn_stream.pack(fill=tk.X, pady=5, ipady=5)

        # 5. Dosya İşlemleri
        ttk.Label(control_frame, text="DOSYA İŞLEMLERİ", font=("Arial", 10, "bold")).pack(pady=(15,5))

        self.btn_save = tk.Button(control_frame, text="SONUÇLARI CSV KAYDET", bg="#2196F3", fg="white", font=("Arial", 10), command=self.save_csv)
        self.btn_save.pack(fill=tk.X, pady=5, ipady=5)

        self.btn_load = tk.Button(control_frame, text="CSV YÜKLE & ÇİZ", bg="#FF9800", fg="white", font=("Arial", 10, "bold"), command=self.load_csv_and_plot)
        self.btn_load.pack(fill=tk.X, pady=5, ipady=5)
        
        self.lbl_status = ttk.Label(control_frame, text="Hazır", foreground="gray")
        self.lbl_status.pack(pady=10)

        # -- Sağ Panel (Grafik) --
        plot_frame = ttk.Frame(top_frame)
        plot_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        
        self.fig = Figure(figsize=(10, 6), dpi=100)
        self.axs = self.fig.subplots(2, 2)
        self.fig.tight_layout(pad=3.0)
        
        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_frame)
        self.canvas.draw()
        
        toolbar = NavigationToolbar2Tk(self.canvas, plot_frame)
        toolbar.update()
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        # -- Alt Panel (Debug Konsolu) --
        bottom_frame = ttk.Frame(main_pane, height=150)
        main_pane.add(bottom_frame, weight=1)

        ttk.Label(bottom_frame, text="İşlem Kayıtları (Log)", font=("Arial", 9, "bold")).pack(anchor="w", padx=5)
        self.log_text = scrolledtext.ScrolledText(bottom_frame, height=8, bg="black", fg="#00FF00", font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.log_text.tag_config("INFO", foreground="#00FF00")
        self.log_text.tag_config("ERROR", foreground="#FF0000")
        self.log_text.tag_config("WARN", foreground="#FFFF00")
        self.log_text.tag_config("FILE", foreground="#00FFFF")
        self.log_text.tag_config("CMD", foreground="#FFA500")

    def add_log(self, msg, type=None):
        if type:
            timestamp = datetime.now().strftime("%H:%M:%S")
            full_msg = f"[{timestamp}] [{type}] {msg}"
            tag = type
        else:
            full_msg = msg
            tag = "INFO"

        self.log_text.insert(tk.END, full_msg + "\n", tag)
        self.log_text.see(tk.END)

    def init_plots(self):
        # -- S11 Smith Chart --
        ax = self.axs[0,0]
        self.draw_smith_background(ax)
        ax.set_title("S11 (Smith Chart)")
        self.lines['S11'], = ax.plot([], [], 'b-', linewidth=1.5, label='S11')

        # -- S12 Log Mag --
        ax = self.axs[0,1]
        ax.set_title("S12 (Reverse)")
        ax.set_xlabel("MHz"); ax.set_ylabel("dB")
        ax.grid(True, linestyle='--', alpha=0.6)
        self.lines['S12'], = ax.plot([], [], 'orange', linewidth=1.5)

        # -- S21 Log Mag --
        ax = self.axs[1,0]
        ax.set_title("S21 (Forward)")
        ax.set_xlabel("MHz"); ax.set_ylabel("dB")
        ax.grid(True, linestyle='--', alpha=0.6)
        self.lines['S21'], = ax.plot([], [], 'green', linewidth=1.5)

        # -- S22 Smith Chart --
        ax = self.axs[1,1]
        self.draw_smith_background(ax)
        ax.set_title("S22 (Smith Chart)")
        self.lines['S22'], = ax.plot([], [], 'r-', linewidth=1.5)

    def draw_smith_background(self, ax):
        ax.clear()
        theta = np.linspace(0, 2*np.pi, 200)
        ax.plot(np.cos(theta), np.sin(theta), color='black', linewidth=1.5)
        
        r_vals = [0.2, 0.5, 1.0, 2.0, 5.0]
        for r in r_vals:
            center_x = r / (r + 1)
            radius = 1 / (r + 1)
            x = center_x + radius * np.cos(theta)
            y = radius * np.sin(theta)
            ax.plot(x, y, color='grey', linestyle=':', linewidth=0.6)

        x_vals = [0.2, 0.5, 1.0, 2.0, 5.0]
        for x_val in x_vals:
            for sign in [1, -1]:
                radius = 1 / x_val
                center_x = 1
                center_y = sign * (1 / x_val)
                x_circle = center_x + radius * np.cos(theta)
                y_circle = center_y + radius * np.sin(theta)
                mask = (x_circle**2 + y_circle**2) <= 1.001
                if np.any(mask):
                    ax.plot(x_circle[mask], y_circle[mask], color='grey', linestyle=':', linewidth=0.6)

        ax.axhline(0, color='black', linewidth=0.5)
        ax.set_aspect('equal')
        ax.set_xlim(-1.1, 1.1)
        ax.set_ylim(-1.1, 1.1)
        ax.grid(False)
        ax.axis('off')

    def load_csv_and_plot(self):
        """CSV dosyasını okur, verileri işler ve grafikleri günceller."""
        if self.is_streaming:
            messagebox.showwarning("Uyarı", "Lütfen önce canlı akışı durdurun.")
            return

        filename = filedialog.askopenfilename(
            title="CSV Dosyası Seç",
            filetypes=[("CSV Dosyaları", "*.csv"), ("Tüm Dosyalar", "*.*")]
        )

        if not filename: return

        try:
            self.add_log(f"Dosya okunuyor: {filename}", "FILE")
            
            s11_data, s12_data, s21_data, s22_data = [], [], [], []

            with open(filename, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                header = next(reader) 
                
                if len(header) < 9:
                    raise ValueError("CSV formatı geçersiz. 9 sütun bekleniyor.")

                for row in reader:
                    if not row: continue
                    try:
                        freq = float(row[0])
                        val11 = complex(float(row[1]), float(row[2]))
                        val12 = complex(float(row[3]), float(row[4]))
                        val21 = complex(float(row[5]), float(row[6]))
                        val22 = complex(float(row[7]), float(row[8]))

                        s11_data.append({'freq': freq, 'val': val11})
                        s12_data.append({'freq': freq, 'val': val12})
                        s21_data.append({'freq': freq, 'val': val21})
                        s22_data.append({'freq': freq, 'val': val22})
                    except ValueError:
                        continue

            if not s11_data:
                raise ValueError("Dosyada geçerli veri bulunamadı.")

            self.latest_data = {
                'S11': s11_data, 'S12': s12_data, 'S21': s21_data, 'S22': s22_data
            }

            self.add_log(f"Veri yüklendi. Nokta sayısı: {len(s11_data)}", "OK")
            self.update_plots_from_memory()
            self.lbl_status.config(text=f"Dosya Görüntüleniyor: {filename.split('/')[-1]}", foreground="blue")

        except Exception as e:
            self.add_log(f"Yükleme Hatası: {e}", "ERROR")
            messagebox.showerror("Hata", f"Dosya yüklenemedi:\n{e}")

    def update_plots_from_memory(self):
        """Hafızadaki (self.latest_data) veriyi kullanarak grafikleri yeniler."""
        
        data_s11 = self.latest_data.get('S11', [])
        if data_s11:
            x = [d['val'].real for d in data_s11]
            y = [d['val'].imag for d in data_s11]
            self.lines['S11'].set_data(x, y)

        data_s22 = self.latest_data.get('S22', [])
        if data_s22:
            x = [d['val'].real for d in data_s22]
            y = [d['val'].imag for d in data_s22]
            self.lines['S22'].set_data(x, y)

        data_s12 = self.latest_data.get('S12', [])
        if data_s12:
            freqs = [d['freq']/1e6 for d in data_s12] # MHz çevrimi
            mags = [20 * math.log10(abs(d['val']) + 1e-12) for d in data_s12] 
            self.lines['S12'].set_data(freqs, mags)
            self.axs[0,1].relim(); self.axs[0,1].autoscale_view()

        data_s21 = self.latest_data.get('S21', [])
        if data_s21:
            freqs = [d['freq']/1e6 for d in data_s21] # MHz çevrimi
            mags = [20 * math.log10(abs(d['val']) + 1e-12) for d in data_s21] 
            self.lines['S21'].set_data(freqs, mags)
            self.axs[1,0].relim(); self.axs[1,0].autoscale_view()

        self.canvas.draw()

    def save_csv(self):
        if not self.latest_data or 'S11' not in self.latest_data:
             messagebox.showwarning("Uyarı", "Henüz kaydedilecek veri yok.")
             return
        
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        initial_file = f"vna_data_{timestamp_str}.csv"
        
        filename = filedialog.asksaveasfilename(
            defaultextension=".csv", 
            initialfile=initial_file,
            filetypes=[("CSV Dosyaları", "*.csv"), ("Tüm Dosyalar", "*.*")],
            title="Ölçüm Sonuçlarını Kaydet"
        )
        
        if not filename: return 
        
        try:
            with open(filename, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                header = ["Freq(Hz)", "S11_Real", "S11_Imag", "S12_Real", "S12_Imag", "S21_Real", "S21_Imag", "S22_Real", "S22_Imag"]
                writer.writerow(header)
                
                s11 = self.latest_data.get('S11', [])
                s12 = self.latest_data.get('S12', [])
                s21 = self.latest_data.get('S21', [])
                s22 = self.latest_data.get('S22', [])
                
                for i in range(len(s11)):
                    freq = s11[i]['freq']
                    val11 = s11[i]['val'] if i < len(s11) else 0j
                    val12 = s12[i]['val'] if i < len(s12) else 0j
                    val21 = s21[i]['val'] if i < len(s21) else 0j
                    val22 = s22[i]['val'] if i < len(s22) else 0j
                    
                    row = [freq, val11.real, val11.imag, val12.real, val12.imag, val21.real, val21.imag, val22.real, val22.imag]
                    writer.writerow(row)
                    
            self.add_log(f"Veriler kaydedildi: {filename}", "FILE")
            messagebox.showinfo("Başarılı", f"Dosya başarıyla kaydedildi:\n{filename}")
            
        except Exception as e:
            self.add_log(f"Kaydetme Hatası: {e}", "ERROR")
            messagebox.showerror("Hata", f"Dosya kaydedilemedi:\n{e}")

    def toggle_streaming(self):
        if not self.is_streaming:
            # BAŞLAT
            try:
                ip = self.entry_ip.get()
                port = int(self.entry_port.get())
                start = parse_frequency(self.entry_start.get())
                stop = parse_frequency(self.entry_stop.get())
                points = int(self.entry_points.get())
                
                # Mevcut Ayarları Oku
                ifbw_text = self.combo_ifbw.get()
                ifbw_val = parse_frequency(ifbw_text) 
                avg_val = int(self.entry_avg.get())
                
                # --- YENİ AYARLARIN OKUNMASI ---
                sweep_type = self.combo_sweep.get() # LIN veya LOG
                
                try:
                    power_val = float(self.entry_power.get())
                except ValueError:
                    power_val = 0 # Hata durumunda güvenli değer
                
                # ------------------------------
                
                if not (start and stop): raise ValueError("Frekans hatası")
                
                self.current_settings = {"start": start, "stop": stop}
                self.add_log(f"Bağlanılıyor... Hedef: {start}-{stop} Hz", "INFO")
                self.root.update()
                
                if not self.client.connect(ip, port):
                    self.add_log("Bağlantı Başarısız!", "ERROR")
                    return
                
                # VNA Modu
                self.client.send_cmd(":DEV:MODE VNA")
                
                # 1. Temel Tarama Ayarları
                self.client.send_cmd(f":VNA:FREQ:START {start}")
                self.client.send_cmd(f":VNA:FREQ:STOP {stop}")
                self.client.send_cmd(f":VNA:ACQ:POINTS {points}")
                
                # 2. Hassasiyet ve Ortalama
                if ifbw_val:
                    self.client.send_cmd(f":VNA:ACQ:IFBW {ifbw_val}")
                
                if avg_val >= 1:
                    self.client.send_cmd(f":VNA:ACQ:AVG {avg_val}")
                
                # 3. YENİ ÖZELLİKLERİN GÖNDERİLMESİ (PDF 4.3.10 ve 4.3.20)
                self.add_log(f"Tip: {sweep_type}, Güç: {power_val} dBm", "CMD")
                self.client.send_cmd(f":VNA:SWEEPTYPE {sweep_type}")
                self.client.send_cmd(f":VNA:STIM:LVL {power_val}")
                
                # 4. Taramayı Başlat
                self.client.send_cmd(":VNA:ACQ:SINGLE FALSE") 
                self.client.send_cmd(":VNA:ACQ:RUN")  
                
                # Trace Kontrolü
                existing_traces = self.client.query(":VNA:TRAC:LIST?") or ""
                for p in ['S11', 'S12', 'S21', 'S22']:
                    if p not in existing_traces:
                        self.client.send_cmd(f":VNA:TRAC:NEW {p}")
                    self.client.send_cmd(f":VNA:TRAC:PARAM {p} {p}")

                self.is_streaming = True
                self.btn_stream.config(text="DURDUR", bg="#d32f2f")
                self.btn_load.config(state="disabled") 
                self.lbl_status.config(text="Canlı Akış Aktif", foreground="green")
                
                self.stream_loop()
                
            except Exception as e:
                self.add_log(f"Başlatma Hatası: {e}", "ERROR")
                messagebox.showerror("Hata", str(e))
        else:
            # DURDUR
            if self.client.sock:
                self.client.send_cmd(":VNA:ACQ:STOP")
                
            self.is_streaming = False
            self.btn_stream.config(text="CANLI AKIŞI BAŞLAT", bg="#4CAF50")
            self.btn_load.config(state="normal") 
            self.lbl_status.config(text="Durduruldu.", foreground="black")
            self.client.disconnect()

    def stream_loop(self):
        if not self.is_streaming: return

        # 1. Verileri Çek
        data_s11 = self.client.get_trace_data("S11")
        data_s12 = self.client.get_trace_data("S12")
        data_s21 = self.client.get_trace_data("S21")
        data_s22 = self.client.get_trace_data("S22")

        # 2. Verileri Hafızaya Al
        if data_s11: self.latest_data['S11'] = data_s11
        if data_s12: self.latest_data['S12'] = data_s12
        if data_s21: self.latest_data['S21'] = data_s21
        if data_s22: self.latest_data['S22'] = data_s22

        # 3. Grafikleri Güncelle
        self.update_plots_from_memory()
        
        # 50ms sonra tekrar
        self.root.after(50, self.stream_loop)

if __name__ == "__main__":
    root = tk.Tk()
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except:
        pass
    app = VNAApp(root)
    root.mainloop()