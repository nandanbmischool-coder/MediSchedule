#!/usr/bin/env python3
"""
gui.py
A minimal Tkinter desktop GUI for the Prescription Label Reader.

This is a thin visual wrapper around the same pipeline app.py uses --
pick an image (or type text), click "Generate", and see the schedule
+ open the generated image/audio files.

Run:
    python gui.py

Requires tkinter (usually bundled with Python; on Linux you may need
`sudo apt-get install python3-tk` -- see README.md).
"""

import os
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext

from src.ocr_engine import extract_text, OCRError
from src.dosage_parser import parse_dosage
from src.schedule_generator import generate_text_schedule, generate_visual_image
from src.voice_output import generate_voice_schedule, VoiceOutputError, is_tts_available

OUTPUT_DIR = "output"


class PrescriptionReaderGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Prescription Label Reader")
        self.root.geometry("640x560")
        self.image_path = None

        tk.Label(root, text="Prescription Label Reader", font=("Arial", 18, "bold")).pack(pady=10)
        tk.Label(root, text="Scan a medicine label photo, or type the text manually.",
                 font=("Arial", 10)).pack()

        # --- Image selection ---
        frame_img = tk.Frame(root)
        frame_img.pack(pady=10, fill="x", padx=20)
        tk.Button(frame_img, text="Choose Label Image...", command=self.choose_image).pack(side="left")
        self.image_label = tk.Label(frame_img, text="No image selected", fg="gray")
        self.image_label.pack(side="left", padx=10)

        tk.Label(root, text="— OR type label text manually —", fg="gray").pack(pady=(10, 0))

        # --- Manual text entry ---
        self.text_entry = tk.Entry(root, width=60)
        self.text_entry.pack(pady=5)
        self.text_entry.insert(0, "e.g. PARACETAMOL 500mg 1-0-1 after food")

        # --- Language selection ---
        frame_lang = tk.Frame(root)
        frame_lang.pack(pady=5)
        tk.Label(frame_lang, text="Voice language:").pack(side="left")
        self.lang_var = tk.StringVar(value="en")
        tk.Radiobutton(frame_lang, text="English", variable=self.lang_var, value="en").pack(side="left")
        tk.Radiobutton(frame_lang, text="Hindi", variable=self.lang_var, value="hi").pack(side="left")

        # --- Generate button ---
        tk.Button(root, text="Generate Schedule", font=("Arial", 12, "bold"),
                  bg="#2e7d32", fg="white", command=self.run_pipeline_threaded).pack(pady=15)

        # --- Output log ---
        self.output_box = scrolledtext.ScrolledText(root, width=74, height=16, font=("Consolas", 10))
        self.output_box.pack(padx=10, pady=5)

        self.status_label = tk.Label(root, text="", fg="blue")
        self.status_label.pack()

    def choose_image(self):
        path = filedialog.askopenfilename(
            title="Select a medicine label photo",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp")]
        )
        if path:
            self.image_path = path
            self.image_label.config(text=os.path.basename(path), fg="black")

    def log(self, msg):
        self.output_box.insert(tk.END, msg + "\n")
        self.output_box.see(tk.END)
        self.root.update_idletasks()

    def run_pipeline_threaded(self):
        # Run in a background thread so the GUI doesn't freeze during OCR/TTS
        threading.Thread(target=self.run_pipeline, daemon=True).start()

    def run_pipeline(self):
        self.output_box.delete("1.0", tk.END)
        self.status_label.config(text="Processing...")

        raw_text = None
        if self.image_path:
            self.log(f"Reading image: {self.image_path}")
            try:
                raw_text = extract_text(self.image_path)
                self.log(f"OCR text:\n{raw_text}\n")
            except OCRError as exc:
                messagebox.showerror("OCR Error", str(exc))
                self.status_label.config(text="Error")
                return
        else:
            typed = self.text_entry.get().strip()
            if typed and not typed.startswith("e.g."):
                raw_text = typed

        if not raw_text:
            messagebox.showwarning("Missing input", "Please choose an image or type the label text.")
            self.status_label.config(text="")
            return

        info = parse_dosage(raw_text)
        schedule_text = generate_text_schedule(info)
        self.log(schedule_text)
        if info.warnings:
            for w in info.warnings:
                self.log(f"WARNING: {w}")

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        img_out = os.path.join(OUTPUT_DIR, "gui_schedule.png")
        try:
            generate_visual_image(info, img_out)
            self.log(f"\nSaved visual schedule: {img_out}")
        except Exception as exc:
            self.log(f"Could not create image: {exc}")

        if is_tts_available():
            wav_out = os.path.join(OUTPUT_DIR, "gui_schedule.wav")
            try:
                generate_voice_schedule(info, wav_out, lang=self.lang_var.get())
                self.log(f"Saved voice file: {wav_out}")
            except VoiceOutputError as exc:
                self.log(f"Could not create voice file: {exc}")
        else:
            self.log("Voice generation skipped: espeak-ng not installed (see README.md).")

        self.status_label.config(text="Done. See output/ folder.")


def main():
    root = tk.Tk()
    PrescriptionReaderGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
