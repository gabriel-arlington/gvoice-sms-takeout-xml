import argparse
import dateutil.parser
import os
import phonenumbers
import re
import time
from datetime import datetime, timedelta
from base64 import b64encode
from bs4 import BeautifulSoup
from io import open  # adds emoji support
from pathlib import Path
from shutil import copyfileobj, move
from tempfile import NamedTemporaryFile
from time import strftime

def convert(in_dir, out_path):

    # Check input directoy:
    html_directory = Path(in_dir)
    assert html_directory.is_dir(), f"Input directory does not exist: {html_directory}"

    # Check output directory:
    output_filepath = Path(out_path)
    assert output_filepath.suffix=='.xml', f"Output file does not have xml extension: {output_filepath}"
    assert output_filepath.parent.exists(), f"Output directory does not exist."
    # Clear file if it already exists
    output_filepath.open("w").close()
    print(f"New file will be saved to {output_filepath}")

    # Initialize counters and start timer:
    start_time=datetime.now()
    print("Checking directory for *.html files")
    num_sms = 0
    num_img = 0
    num_vcf = 0
    own_number = None

    # # Function build a mapping of HTML filename to the img src it contains -- for DEBUGGING.
    # def build_filename_src_map(html_directory):
    #     mapping = {}
    #     for html_file in Path(html_directory).rglob('*.html'):  # Assuming HTML files have .html extension
    #         file_srcs = []
    #         with open(html_file, 'r', encoding='utf-8') as file:
    #             soup = BeautifulSoup(file, 'html.parser')
    #             file_srcs.extend([img['src'] for img in soup.find_all('img') if 'src' in img.attrs])
    #             file_srcs.extend([a['href'] for a in soup.find_all('a', class_='vcard') if 'href' in a.attrs])
    #         mapping[str(html_file.name)] = file_srcs
    #     return mapping
    # filename_src_map = build_filename_src_map(html_directory)
    # import json
    # with open('filename_src_map.json','w') as f:
    #     json.dump(filename_src_map, f)

    # Create the src to filename mapping
    src_elements = extract_src(html_directory)  # Assuming current directory
    att_filenames = list_att_filenames(html_directory)    # Assuming current directory
    num_img = sum(1 for filename in att_filenames if Path(filename).suffix.lower() in {'.jpg', '.jpeg', '.png', '.gif'})
    num_vcf = sum(1 for filename in att_filenames if Path(filename).suffix.lower() == '.vcf')
    src_filename_map = src_to_filename_mapping(src_elements, att_filenames)

    # Loop through files in HTML directory (assuming no subfolders):
    for html_filename in os.listdir(html_directory):
        html_filepath = Path(html_directory)/html_filename

        if html_filepath.suffix != ".html":
            #print(sms_filename,"- skipped")
            continue
        if html_filepath.name == "archive_browser.html":
            #print(sms_filename,"- skipped")
            continue

        print(f"Processing {html_filepath}")

        is_group_conversation = re.match(r"(^Group Conversation)", str(html_filepath))

        with open(html_filepath, "r", encoding="utf8") as sms_file:
            soup = BeautifulSoup(sms_file, "html.parser")

        messages_raw = soup.find_all(class_="message")
        # Extracting own phone number if the <abbr> tag with class "fn" contains "Me"
        for abbr_tag in soup.find_all('abbr', class_='fn'):
            if abbr_tag.get_text(strip=True) == "Me":
                a_tag = abbr_tag.find_previous('a', class_='tel')
            if a_tag:
                own_number = a_tag.get('href').split(':', 1)[-1]  # Extracting number from href
                break
        # Skip files with no messages
        if not len(messages_raw):
            continue

        num_sms += len(messages_raw)

        if is_group_conversation:
            participants_raw = soup.find_all(class_="participants")
            write_mms_messages(html_filepath, participants_raw, messages_raw, own_number, src_filename_map, output_filepath)
        else:
            write_sms_messages(html_filepath, messages_raw, own_number, src_filename_map, output_filepath)

    sms_backup_file = open(output_filepath, "a")
    sms_backup_file.write("</smses>")
    sms_backup_file.close()
    end_time=datetime.now()
    elapsed_time = end_time - start_time
    total_seconds = int(elapsed_time.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    parts = []
    if hours > 0:
        hour_str = "hour" if hours == 1 else "hours"
        parts.append(f"{hours} {hour_str}")
    if minutes > 0:
        minute_str = "minute" if minutes == 1 else "minutes"
        parts.append(f"{minutes} {minute_str}")
    if seconds > 0 or (hours == 0 and minutes == 0):
        second_str = "second" if seconds == 1 else "seconds"
        parts.append(f"{seconds} {second_str}")
    time_str = ", ".join(parts)
    print(f"Processed {num_sms} messages, {num_img} images, and {num_vcf} contact cards in {time_str}")    
    write_header(output_filepath, num_sms)

# Fixes special characters in the vCards
def escape_xml(s):
    return (s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("'", "&apos;")
            .replace('"', "&quot;"))

# Function to extract img src from HTML files
def extract_src(html_directory):
    src_list = []
    for html_file in Path(html_directory).rglob('*.html'):  # Assuming HTML files have .html extension
        with open(html_file, 'r', encoding='utf-8') as file:
            soup = BeautifulSoup(file, 'html.parser')
            src_list.extend([img['src'] for img in soup.find_all('img') if 'src' in img.attrs])
            src_list.extend([a['href'] for a in soup.find_all('a', class_='vcard') if 'href' in a.attrs])
    return src_list

# Function to list attachment filenames with specific extensions
def list_att_filenames(image_directory):
    allowed_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.vcf'}
    matched_filenames = [
        str(path.name) for path in Path(image_directory).rglob('*') 
        if path.suffix.lower() in allowed_extensions
    ]
    return matched_filenames

# Function to remove file extension and parenthesized numbers from the end of image filenames. This is used to match those filenames back to their respective img_src key.
def normalize_filename(filename):
    # Remove the file extension and any parenthesized numbers, then truncate at 50 characters
    return re.sub(r'(?:\((\d+)\))?\.(jpg|gif|png|vcf)$', '', filename)[:50]

# Function to sort filenames so that files with parenthesized numbers appended to the end follow the base filename.
def custom_filename_sort(filename):
    # This will match the entire filename up to the extension, and capture any numbers in parentheses
    match = re.match(r'(.*?)(?:\((\d+)\))?(\.\w+)?$', str(filename))
    if match:
        base_filename = match.group(1)
        number = int(match.group(2)) if match.group(2) else -1  # Assign -1 to filenames without parentheses
        extension = match.group(3) if match.group(3) else ''  # Some filenames may not have an extension
        return (base_filename, number, extension)
    else:
        # If there's no match (which should not happen with the filenames you provided),
        # return a tuple that sorts it last
        return (filename, float('inf'), '')

# Function to produce a dictionary that maps img src elements (which are unique) to the respective filenames.
def src_to_filename_mapping(src_elements, att_filenames):
    used_filenames = set()
    mapping = {}
    for src in src_elements:
        att_filenames.sort(key=custom_filename_sort)  # Sort filenames before matching
        assigned_filename = None
        for filename in att_filenames:
            normalized_filename = normalize_filename(filename)
            if normalized_filename in src and filename not in used_filenames:
                assigned_filename = filename
                used_filenames.add(filename)
                break
        assert src not in mapping.keys(), f"Found src that is not unique: {src}."
        mapping[src] = assigned_filename if assigned_filename else 'no_match_found'  # If no unused match found, placeholder.
    return mapping

def write_sms_messages(html_filepath, messages_raw, own_number, src_filename_map, output_filepath):
    fallback_number = 0
    title_has_number = re.search(r"(^\+[0-9]+)", Path(html_filepath).name)
    if title_has_number:
        fallback_number = title_has_number.group()

    phone_number, participant_raw = get_first_phone_number(
        messages_raw, fallback_number
    )

    # Make sure filename is a Path object and get its parent directory (containing html files, images, vcards):
    html_filepath = Path(html_filepath)
    html_directory = Path(html_filepath).parent

    # Search similarly named files for a fallback number. This is desperate and expensive, but
    # hopefully rare.
    if phone_number == 0:
        file_prefix = "-".join(html_filepath.stem.split("-")[0:1])
        for fallback_file in html_directory.glob(f"**/{file_prefix}*.html"):
            with fallback_file.open("r", encoding="utf8") as ff:
                soup = BeautifulSoup(ff, "html.parser")
            messages_raw_ff = soup.find_all(class_="message")
            phone_number, participant_raw = get_first_phone_number(messages_raw_ff, 0)
            if phone_number != 0:
                break

    # Start looking in the Placed/Received files for a fallback number
    if phone_number == 0:
        file_prefix = f'{html_filepath.stem.split(" - ")[0]} - '
        for fallback_file in html_directory.glob(f"**/{file_prefix}*.html"):
            with fallback_file.open("r", encoding="utf8") as ff:
                soup = BeautifulSoup(ff, "html.parser")
            contrib_vcards = soup.find_all(class_="contributor vcard")
            phone_number_ff = 0
            for contrib_vcard in contrib_vcards:
                phone_number_ff = contrib_vcard.a["href"][4:]
            phone_number, participant_raw = get_first_phone_number([], phone_number_ff)
            if phone_number != 0:
                break

    sms_values = {"phone": phone_number}

    sms_backup_file = open(output_filepath, "a", encoding="utf8")

    for message in messages_raw:
        # Check if message has an image or vCard in it and treat as mms if so
        if message.find_all("img"):
            write_mms_messages(html_filepath, [[participant_raw]], [message], own_number, src_filename_map, output_filepath)
            continue
        if message.find_all("a", class_='vcard'):
            write_mms_messages(html_filepath, [[participant_raw]], [message], own_number, src_filename_map, output_filepath)
            continue
        message_content = get_message_text(message)
        if message_content == "MMS Sent" or message_content == "MMS Received":
            continue
        sms_values["type"] = get_message_type(message)
        sms_values["message"] = message_content
        sms_values["time"] = get_time_unix(message)
        sms_text = (
            '<sms protocol="0" address="%(phone)s" '
            'date="%(time)s" type="%(type)s" '
            'subject="null" body="%(message)s" '
            'toa="null" sc_toa="null" service_center="null" '
            'read="1" status="1" locked="0" /> \n' % sms_values
        )
        sms_backup_file.write(sms_text)

    sms_backup_file.close()

def write_mms_messages(html_filepath, participants_raw, messages_raw, own_number, src_filename_map, output_filepath):
    sms_backup_file = open(output_filepath, "a", encoding="utf8")

    participants = get_participant_phone_numbers(participants_raw)
    participants_text = "~".join(participants)

    # Adding own_number to participants if it exists and is not already in the list

    # Make sure filename is a Path object and get its parent directory (containing html files, images, vcards):
    html_filepath = Path(html_filepath)
    html_directory = Path(html_filepath).parent
    
    for message in messages_raw:
        # Sometimes the sender tel field is blank. Try to guess the sender from the participants.
        sender = get_mms_sender(message, participants)
        sent_by_me = sender==own_number
        if own_number not in participants:
            participants.append(own_number)
        
        # Handle images and vcards
        images = message.find_all("img")
        image_parts = ""
        vcards = message.find_all("a", class_='vcard')
        vcards_parts = ""
        extracted_url = ""
        if images:
            text_only=0
            for image in images:
                # I have only encountered jpg and gif, but I have read that GV can ecxport png
                supported_image_types = ["jpg", "png", "gif"]
                image_src = image["src"]
                # Change to use the src_filename_map to find the image filename that corresponds to the image_src value, which is unique to each image MMS message.
                original_image_filename = src_filename_map.get(image_src, "no_match_found")  # Use a default filename if not found.
                if original_image_filename=="no_match_found":
                    original_image_filename = image_src  # If no filename was found, revert to src name.
                image_filename_without_ext = Path(original_image_filename).stem
                # Create list to store potential matches -- at the end we expect exactly one match:
                image_path = []

                if (len(image_path) == 0) and (original_image_filename != "no_match_found"):
                    # Sometimes the match is exact:
                    image_path = list(
                        html_directory.glob(f"**/{original_image_filename}")
                    )
                    image_path = [p for p in image_path if p.is_file() and p.suffix[1:] in supported_image_types]

                if (len(image_path) == 0) and (original_image_filename != "no_match_found"):
                    # Sometimes they just forget the extension
                    image_path = list(
                        html_directory.glob(f"**/{image_filename_without_ext}.*")
                    )
                    image_path = [p for p in image_path if p.is_file() and p.suffix[1:] in supported_image_types]

                if (len(image_path) == 0) and (original_image_filename != "no_match_found"):
                    # Sometimes the first word doesn't match (eg it is a phone number instead of a
                    # contact name) so try again without the first word
                    # (Skip this attempt if filename doesn't have a dash)
                    filename_parts = image_filename_without_ext.split(" - ")
                    if len(filename_parts)>1:
                        shortened_image_filename = " - ".join(filename_parts[1:])
                        image_path = list(
                            html_directory.glob(f"**/* - {shortened_image_filename}*")
                        )
                        image_path = [p for p in image_path if p.is_file() and p.suffix[1:] in supported_image_types]

                if len(image_path) == 0:
                    # Sometimes the image filename matches the message filename instead of the
                    # filename in the HTML. And sometimes the message filenames are repeated, eg
                    # filefoo(0).html, filefoo(1).html, etc., but the image filename matches just
                    # the base ("filefoo" in this example).
                    modified_image_filenames = [Path(html_filepath).stem, Path(html_filepath).stem.split("(")[0]]
                    for modified_image_filename in modified_image_filenames:
                        # Have to guess at the file extension in this case
                        for supported_type in supported_image_types:
                            image_path = list(
                                html_directory.glob(f"**/{modified_image_filename}*.{supported_type}")
                            )
                            image_path = [p for p in image_path if p.is_file()]
                            # Sometimes there's extra cruft in the filename in the HTML. So try to
                            # match a subset of it.
                            if len(image_path) > 1:
                                for ip in image_path:
                                    if ip.stem in original_image_filename:
                                        image_path = [ip]
                                        break
                            if len(image_path) == 1:
                                break
                        if len(image_path) == 1:
                            break

                assert (
                    len(image_path) != 0
                ), f"No matching images found for src '{image_src}' in '{str(html_filepath.name)}'"
                assert (
                    len(image_path) == 1
                ), f"Multiple potential matching images found for src '{image_src}' in '{str(html_filepath.name)}'. Images: {[str(x.name) for x in image_path]!r}"

                image_path = image_path[0]
                image_type = image_path.suffix[1:]
                image_type = "jpeg" if image_type == "jpg" else image_type

                with image_path.open("rb") as fb:
                    image_bytes = fb.read()
                byte_string = f"{b64encode(image_bytes)}"

                # Use the full path and then derive the relative path, ensuring the complete filename is used
                relative_image_path = image_path.relative_to(html_directory)
    
                image_parts += (
                    f'    <part seq="0" ct="image/{image_type}" name="{relative_image_path}" '
                    f'chset="null" cd="null" fn="null" cid="&lt;{relative_image_path}&gt;" '
                    f'cl="{relative_image_path}" ctt_s="null" ctt_t="null" text="null" '
                    f'data="{byte_string[2:-1]}" />\n'
                )
        
        # Handle vcards
        if vcards:
            #continue
            text_only=0
            for vcard in vcards:
                # I have only encountered jpg and gif, but I have read that GV can ecxport png
                supported_vcards_types = ["vcf"]
                vcards_src = vcard.get("href")
                # Change to use the src_filename_map to find the vcards filename that corresponds to the vcards_src value, which is unique to each vcards MMS message.
                original_vcards_filename = src_filename_map.get(vcards_src, "no_match_found")  # Use a default filename if not found.
                if original_vcards_filename=="no_match_found":
                    original_vcards_filename = vcards_src  # If no filename was found, revert to src name.
                vcards_filename_without_ext = Path(original_vcards_filename).stem
                # Create list to store potential matches -- at the end we expect exactly one match:
                vcards_path = []
                    
                if (len(vcards_path) == 0) and (original_vcards_filename != "no_match_found"):
                    # Sometimes the match is exact:
                    vcards_path = list(
                        html_directory.glob(f"**/{original_vcards_filename}")
                    )
                    vcards_path = [p for p in vcards_path if p.is_file() and p.suffix[1:] in supported_vcards_types]

                if (len(vcards_path) == 0) and (original_vcards_filename != "no_match_found"):
                    # Sometimes they just forget the extension
                    vcards_path = list(
                        html_directory.glob(f"**/{vcards_filename_without_ext}.*")
                    )
                    vcards_path = [p for p in vcards_path if p.is_file() and p.suffix[1:] in supported_vcards_types]

                if (len(vcards_path) == 0) and (original_vcards_filename != "no_match_found"):
                    # Sometimes the first word doesn't match (eg it is a phone number instead of a
                    # contact name) so try again without the first word
                    # (Skip this attempt if filename doesn't have a dash)
                    filename_parts = vcards_filename_without_ext.split(" - ")
                    if len(filename_parts)>1:
                        shortened_vcards_filename = " - ".join(filename_parts[1:])
                        vcards_path = list(
                            html_directory.glob(f"**/* - {shortened_vcards_filename}*")
                        )
                        vcards_path = [p for p in vcards_path if p.is_file() and p.suffix[1:] in supported_vcards_types]
                
                if len(vcards_path) == 0:
                    # Sometimes the vcards filename matches the message filename instead of the
                    # filename in the HTML. And sometimes the message filenames are repeated, eg
                    # filefoo(0).html, filefoo(1).html, etc., but the vcards filename matches just
                    # the base ("filefoo" in this example).
                    modified_vcards_filenames = [Path(html_filepath).stem, Path(html_filepath).stem.split("(")[0]]
                    for modified_vcards_filename in modified_vcards_filenames:
                        # Have to guess at the file extension in this case
                        for supported_type in supported_vcards_types:
                            vcards_path = list(
                                html_directory.glob(f"**/{modified_vcards_filename}*.{supported_type}")
                            )
                            vcards_path = [p for p in vcards_path if p.is_file()]
                            # Sometimes there's extra cruft in the filename in the HTML. So try to
                            # match a subset of it.
                            if len(vcards_path) > 1:
                                for ip in vcards_path:
                                    if ip.stem in original_vcards_filename:
                                        vcards_path = [ip]
                                        break
                            if len(vcards_path) == 1:
                                break
                        if len(vcards_path) == 1:
                            break

                assert (
                    len(vcards_path) != 0
                ), f"No matching vcards found for src '{vcards_src}' in {original_vcards_filename}"
                assert (
                    len(vcards_path) == 1
                ), f"Multiple potential matching vcards found for src '{vcards_src}' in {original_vcards_filename}. vcards: {[x for x in vcards_path]!r}"

                vcards_path = vcards_path[0]
                vcards_type = vcards_path.suffix[1:]
                
                # This section searches for any contact cards that are just location pins, and turns them into a plain text MMS message with the URL for the pin.
                # If you don't want to perform this conversion, then comment out this section.
                with vcards_path.open("r", encoding="utf-8") as fb:
                    current_location_found = False
                    for line in fb:
                        if line.startswith("FN:") and "Current Location" in line:
                            current_location_found = True
                        if current_location_found and line.startswith("URL;type=pref:"):
                            extracted_url = line.split(":", 1)[1].strip()
                            extracted_url = extracted_url.replace("\\", "")  # Remove backslashes
                            extracted_url = escape_xml(extracted_url)
                            break

                    if not current_location_found:
                        with vcards_path.open("rb") as fb:
                            vcards_bytes = fb.read()
                            byte_string = f"{b64encode(vcards_bytes)}"

                            # Use the full path and then derive the relative path, ensuring the complete filename is used
                            relative_vcards_path = vcards_path.relative_to(html_directory)
    
                            vcards_parts += (
                            f'    <part seq="0" ct="text/x-vCard" name="{relative_vcards_path}" '
                            f'chset="null" cd="null" fn="null" cid="&lt;{relative_vcards_path}&gt;" '
                            f'cl="{relative_vcards_path}" ctt_s="null" ctt_t="null" text="null" '
                            f'data="{byte_string[2:-1]}" />\n'
                        )

                # If you don't want to convert vcards with locations to plain text MMS, uncomment this section.
                #with vcards_path.open("rb") as fb:
                    #vcards_bytes = fb.read()
                    #byte_string = f"{b64encode(vcards_bytes)}"
                    # Use the full path and then derive the relative path, ensuring the complete filename is used
                    #relative_vcards_path = vcards_path.relative_to(html_directory)
                    #vcards_parts += (
                    #f'    <part seq="0" ct="text/x-vCard" name="{relative_vcards_path}" '
                    #f'chset="null" cd="null" fn="null" cid="&lt;{relative_vcards_path}&gt;" '
                    #f'cl="{relative_vcards_path}" ctt_s="null" ctt_t="null" text="null" '
                    #f'data="{byte_string[2:-1]}" />\n'
                #)
        else:
            text_only=1
        if extracted_url:
            message_text = "Dropped pin&#10;" + extracted_url
        else:
            message_text = get_message_text(message)
        #message_text = get_message_text(message)
        time = get_time_unix(message)
        participants_xml = ""
        msg_box = 2 if sent_by_me else 1
        m_type = 128 if sent_by_me else 132
        for participant in participants:
            participant_is_sender = participant == sender or (
                sent_by_me and participant == "Me"
            )
            participant_values = {
                "number": participant,
                "code": 137 if participant_is_sender else 151,
            }
            participants_xml += (
                '    <addr address="%(number)s" charset="106" type="%(code)s"/> \n'
                % participant_values
            )

            mms_text = (
                f'<mms address="{participants_text}" ct_t="application/vnd.wap.multipart.related" '
                f'date="{time}" m_type="{m_type}" msg_box="{msg_box}" read="1" '
                f'rr="129" seen="1" sim_slot="1" sub_id="-1" text_only="{text_only}"> \n'
                "  <parts> \n"
            )

            # This skips the plain text part in an MMS message if it contains the phrases "MMS Sent" or "MMS Received".
            if message_text not in ["MMS Sent", "MMS Received"]:
                mms_text += f'    <part ct="text/plain" seq="0" text="{message_text}"/> \n'

            mms_text += image_parts
            mms_text += vcards_parts

            mms_text += (
                "  </parts> \n"
                "  <addrs> \n"
                f"{participants_xml}"
                "  </addrs> \n"
                "</mms> \n"
                )

        sms_backup_file.write(mms_text)

    sms_backup_file.close()

def get_message_type(message):  # author_raw = messages_raw[i].cite
    author_raw = message.cite
    if not author_raw.span:
        return 2
    else:
        return 1

    return 0

def get_message_text(message):
    # Attempt to properly translate newlines. Might want to translate other HTML here, too.
    # This feels very hacky, but couldn't come up with something better.
    # Added additional replace() calls to strip out special character that were causing issues with importing the XML file.
    message_text = str(message.find("q")).strip()[3:-4].replace("<br/>", "&#10;").replace("'", "&apos;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")

    return message_text

def get_mms_sender(message, participants):
    number_text = message.cite.a["href"][4:]
    if number_text != "":
        number = format_number(phonenumbers.parse(number_text, None))
    else:
        assert (
            len(participants) == 1
        ), f"Unable to determine sender in mms with multiple participants: {participants}"
        number = participants[0]
    return number

def get_first_phone_number(messages, fallback_number):
    # handle group messages
    for author_raw in messages:
        if not author_raw.span:
            continue

        sender_data = author_raw.cite
        # Skip if first number is Me
        if sender_data.text == "Me":
            continue
        phonenumber_text = sender_data.a["href"][4:]
        # Sometimes the first entry is missing a phone number
        if phonenumber_text == "":
            continue

        try:
            phone_number = phonenumbers.parse(phonenumber_text, None)
        except phonenumbers.phonenumberutil.NumberParseException:
            return phonenumber_text, sender_data

        # sender_data can be used as participant for mms
        return format_number(phone_number), sender_data

    # fallback case, use number from filename
    if fallback_number != 0 and len(fallback_number) >= 7:
        fallback_number = format_number(phonenumbers.parse(fallback_number, None))
    # Create dummy participant
    sender_data = BeautifulSoup(
        f'<cite class="sender vcard"><a class="tel" href="tel:{fallback_number}"><abbr class="fn" '
        'title="">Foo</abbr></a></cite>',
        features="html.parser",
    )
    return fallback_number, sender_data

def get_participant_phone_numbers(participants_raw):
    participants = []

    for participant_set in participants_raw:
        for participant in participant_set:
            if not hasattr(participant, "a"):
                continue

            phone_number_text = participant.a["href"][4:]
            assert (
                phone_number_text != "" and phone_number_text != "0"
            ), "Could not find participant phone number. Usually caused by empty tel field."
            try:
                participants.append(
                    format_number(phonenumbers.parse(phone_number_text, None))
                )
            except phonenumbers.phonenumberutil.NumberParseException:
                participants.append(phone_number_text)

    return participants

def format_number(phone_number):
    return phonenumbers.format_number(phone_number, phonenumbers.PhoneNumberFormat.E164)

def get_time_unix(message):
    time_raw = message.find(class_="dt")
    ymdhms = time_raw["title"]
    time_obj = dateutil.parser.isoparse(ymdhms)
    # Changed this line to get the full date value including milliseconds.
    mstime = time.mktime(time_obj.timetuple()) * 1000 + time_obj.microsecond // 1000
    return int(mstime)

def write_header(output_filepath, numsms):
    # Make sure filename is a Path object and get its parent directory (containing html files, images, vcards):
    output_filepath = Path(output_filepath)
    output_directory = Path(output_filepath).parent
    # Prepend header in memory efficient manner since the output file can be huge
    with NamedTemporaryFile(dir=output_directory, delete=False) as backup_temp:
        backup_temp.write(b"<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>\n")
        backup_temp.write(b"<!--Converted from GV Takeout data -->\n")
        backup_temp.write(bytes(f'<smses count="{str(numsms)}">\n', encoding="utf8"))
        with open(output_filepath, "rb") as backup_file:
            copyfileobj(backup_file, backup_temp)
    # Overwrite output file with temp file
    move(backup_temp.name, output_filepath)

if __name__=="__main__":

    # Define and parse arguments:
    default_in_dir = '.'
    default_out_dir = './gvoice-all.xml'
    parser = argparse.ArgumentParser(
        prog='gvoice-sms-takout-xml',
        description='Convert Google Voice Takout archive to XML format for SMS Backup & Restore.',
        epilog='https://github.com/SLAB-8002/gvoice-sms-takeout-xml'
    )
    parser.add_argument('--in_dir', '-i', type=str, default=default_in_dir, help=f"Path to the directory that contains .html files. Default: {default_in_dir}")
    parser.add_argument('--out_path', '-o', type=str, default=default_out_dir, help=f"Path to the .xml output file of converted messages. Default: {default_out_dir}")
    args = parser.parse_args()

    # Run script:
    convert(in_dir=args.in_dir, out_path=args.out_path)
