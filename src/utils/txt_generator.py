import json
import os
import tempfile


async def txt_generator(transcriptions):
    """
    Generates a TXT file with aligned transcription columns, wrapping long data entries, and reduced margins.
    
    :param transcriptions: List of transcriptions to include in the PDF.
    :return: Path to the generated PDF file.
    """
   
    logs_dir = "./.logs/texts"
    os.makedirs(logs_dir, exist_ok=True)

   
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt", dir=logs_dir, mode="w", encoding="utf-8") as tmp_file:
        txt_file_path = tmp_file.name

        tmp_file.write("Conference Transcription\n")
        tmp_file.write("========================\n\n")
   
        for log_message in transcriptions:
            if isinstance(log_message, str):
                try:
                    log_message = json.loads(log_message)
                except json.JSONDecodeError:
                    continue
            
            begin = log_message.get("begin", "N/A")
            display_name = log_message.get("displayName", "N/A")
            data = log_message.get("data", "")

            
            tmp_file.write(f"Time: {begin}\n")
            tmp_file.write(f"User: {display_name}\n")
            tmp_file.write(f"Message: {data}\n")
            tmp_file.write("-------------------------\n")

    return txt_file_path
