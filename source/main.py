from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room
from azure.storage.blob import BlobServiceClient

import smtplib
import random
from email.mime.text import MIMEText

#
from dotenv import load_dotenv
import os
import datetime
import json
import base64
from flask import send_from_directory, redirect
import uuid

# Clave AES global para cifrado de mensajes (ejemplo de 32 bytes para AES-256)
# NOTA: En producción, esta clave debe mantenerse secreta y nunca exponerse.
GLOBAL_AES_KEY = b"0123456789abcdef0123456789abcdef"

# =========================
# Variables globales para recuperación de contraseña
password_reset_codes = {}

# =========================
# Utilidad para enviar correo de recuperación
def send_recovery_email(to_email, code):
    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port = int(os.getenv("SMTP_PORT"))
    email_user = os.getenv("EMAIL_USER")
    email_pass = os.getenv("EMAIL_PASS")
    msg = MIMEText(f"Tu código de recuperación es: {code}")
    msg['Subject'] = "Código de recuperación - ChatApp"
    msg['From'] = email_user
    msg['To'] = to_email
    with smtplib.SMTP(smtp_server, smtp_port) as server:
        server.starttls()
        server.login(email_user, email_pass)
        server.sendmail(email_user, [to_email], msg.as_string())


load_dotenv(dotenv_path=".env")
AZURE_CONNECTION_STRING = os.getenv("AZURE_CONNECTION_STRING")
USERS_CONTAINER = os.getenv("USERS_CONTAINER")
MESSAGES_CONTAINER = os.getenv("MESSAGES_CONTAINER")

app = Flask(__name__)
CORS(
    app,
    # resources={r"/*": {"origins": "http://10.48.73.169:5050"}},
    resources={r"/*": {"origins": "http://10.48.73.169:5050"}},
    supports_credentials=True,
)
# socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

socketio = SocketIO(
    # app, cors_allowed_origins="http://10.48.73.169:5050", async_mode="threading"
    app,
    cors_allowed_origins="http://10.48.73.169:5050",
    async_mode="threading",
)

blob_service_client = BlobServiceClient.from_connection_string(AZURE_CONNECTION_STRING)
usuarios_container = blob_service_client.get_container_client(USERS_CONTAINER)
mensajes_container = blob_service_client.get_container_client(MESSAGES_CONTAINER)


@app.route("/get-global-aes", methods=["GET"])
def get_global_aes():
    # Solo para desarrollo. En producción, restringe este endpoint.
    return jsonify({"key": base64.b64encode(GLOBAL_AES_KEY).decode()}), 200


def get_blob_name(email):
    return f"{email.strip().lower()}.json"


def get_chat_id(user1, user2):
    return "-".join(sorted([user1.strip().lower(), user2.strip().lower()]))


@app.route("/")
def index():
    return send_from_directory("index", "index.html")


@app.route("/get-public-key", methods=["GET"])
def get_public_key():
    email = request.args.get("email", "").strip().lower()
    if not email:
        return jsonify({"error": "Falta el email"}), 400
    try:
        blob_name = get_blob_name(email)
        blob_client = usuarios_container.get_blob_client(blob_name)
        if not blob_client.exists():
            return jsonify({"error": "Usuario no encontrado"}), 404
        user_data = json.loads(blob_client.download_blob().readall())
        public_key = user_data.get("public_key")
        if not public_key:
            return jsonify({"error": "Clave pública no encontrada"}), 404
        return jsonify({"public_key": public_key}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/style.css")
def serve_style():
    return send_from_directory("index", "style.css")

@app.route("/source/dashboard.html")
def dashboard():
    user_email = request.cookies.get("user_email")
    if not user_email:
        return redirect("/"), 302
    return send_from_directory(".", "dashboard.html")


@app.route("/source/style.css")
def styles():
    return send_from_directory(".", "style.css")


@app.route("/get-group-messages", methods=["POST"])
def get_group_messages():
    data = request.json
    group_id = data.get("group_id")
    if not group_id:
        return jsonify({"error": "Falta el id de grupo"}), 400

    blob_name = f"chat_{group_id}.json"
    blob_client = mensajes_container.get_blob_client(blob_name)

    try:
        if not blob_client.exists():
            return jsonify({"messages": []}), 200

        messages = json.loads(blob_client.download_blob().readall())
        return jsonify({"messages": messages}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@socketio.on("send_message")
def handle_send_message(data):
    """
    En esta versión, el backend NO cifra ni descifra mensajes.
    Solo almacena y reenvía los mensajes tal como llegan (ya cifrados por el frontend).
    """
    sender = data["from"]
    recipient = data["to"]
    content = data["message"]
    msg_type = data.get("type")
    chat_id = get_chat_id(sender, recipient)

    # El backend almacena y reenvía el mensaje tal cual (ya cifrado).
    msg = {"from": sender, "to": recipient, "message": content}
    if msg_type:
        msg["type"] = msg_type
    emit("receive_message", msg, room=chat_id)

    try:
        blob_name = f"chat_{min(sender, recipient)}__{max(sender, recipient)}.json"
        blob_client = mensajes_container.get_blob_client(blob_name)

        all_messages = []
        if blob_client.exists():
            all_messages = json.loads(blob_client.download_blob().readall())

        msg_to_store = {
            "from": sender,
            "to": recipient,
            "message": content,
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "read": False,
        }
        if msg_type:
            msg_to_store["type"] = msg_type

        all_messages.append(msg_to_store)
        blob_client.upload_blob(json.dumps(all_messages), overwrite=True)

        # Emitir notificación si el destinatario no está en ese chat
        active_blob = usuarios_container.get_blob_client(get_blob_name(recipient))
        if active_blob.exists():
            user_data = json.loads(active_blob.download_blob().readall())
            active_with = user_data.get("active_with")

            if active_with != sender:
                socketio.emit("notify_unread", {"from": sender}, room=recipient)

    except Exception as e:
        print(f"[ERROR] No se pudo guardar mensaje en Azure: {e}")


@socketio.on("join_chat")
def handle_join(data):
    """
    En esta versión, el backend NO cifra ni descifra mensajes.
    Solo carga y reenvía el historial de mensajes tal como está (ya cifrados por el frontend).
    """
    user1 = data["user1"].strip().lower()
    user2 = data["user2"].strip().lower()

    chat_id = get_chat_id(user1, user2)
    join_room(chat_id)
    join_room(user1)  # 🔔 sala personalizada del usuario
    join_room(user2)  # también se une el otro

    print(f"[JOIN] {request.sid} se unió a {chat_id}, {user1}, y {user2}")

    try:
        blob_name = f"chat_{min(user1, user2)}__{max(user1, user2)}.json"
        blob_client = mensajes_container.get_blob_client(blob_name)

        messages = []
        if blob_client.exists():
            messages = json.loads(blob_client.download_blob().readall())

        emit("chat_history", messages, room=request.sid)

    except Exception as e:
        print(f"[ERROR] No se pudo cargar historial desde Azure: {e}")
        emit("chat_history", [], room=request.sid)


@socketio.on("update_active_chat")
def handle_update_active_chat(data):
    user = data.get("user", "").strip().lower()
    active_with = data.get("active_with", "").strip().lower()

    try:
        blob_client = usuarios_container.get_blob_client(get_blob_name(user))
        if not blob_client.exists():
            print(f"[WARN] Usuario {user} no encontrado para actualizar active_with.")
            return

        user_data = json.loads(blob_client.download_blob().readall())
        user_data["active_with"] = active_with

        blob_client.upload_blob(json.dumps(user_data), overwrite=True)
        print(f"[INFO] Usuario {user} ahora activo con {active_with}")
    except Exception as e:
        print(f"[ERROR] No se pudo actualizar active_with para {user}: {e}")


@app.route("/send-request", methods=["POST"])
def send_request():
    data = request.json
    from_email = data.get("from_email", "").strip().lower()
    to_email = data.get("to_email", "").strip().lower()

    if from_email == to_email:
        return jsonify({"error": "No puedes enviarte una solicitud a ti mismo"}), 400

    try:
        from_blob = usuarios_container.get_blob_client(get_blob_name(from_email))
        to_blob = usuarios_container.get_blob_client(get_blob_name(to_email))

        if not from_blob.exists() or not to_blob.exists():
            return jsonify({"error": "Uno de los usuarios no existe"}), 404

        from_data = json.loads(from_blob.download_blob().readall())
        to_data = json.loads(to_blob.download_blob().readall())

        if to_email in from_data.get("contacts", []):
            return jsonify({"message": "Ya es tu contacto"}), 200

        if from_email in to_data.get("requests", []):
            return jsonify({"message": "Solicitud ya enviada"}), 200

        if to_email in from_data.get("requests", []):
            return jsonify({"message": "Este usuario ya te envió una solicitud"}), 200

        to_data["requests"].append(from_email)
        to_blob.upload_blob(json.dumps(to_data), overwrite=True)

        return jsonify({"message": "Solicitud enviada correctamente"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/respond-request", methods=["POST"])
def respond_request():
    data = request.json
    user_email = data.get("user_email", "").strip().lower()
    requester_email = data.get("requester_email", "").strip().lower()
    accepted = data.get("accepted", False)

    try:
        user_blob = usuarios_container.get_blob_client(get_blob_name(user_email))
        requester_blob = usuarios_container.get_blob_client(
            get_blob_name(requester_email)
        )

        if not user_blob.exists() or not requester_blob.exists():
            return jsonify({"error": "Uno de los usuarios no existe"}), 404

        user_data = json.loads(user_blob.download_blob().readall())
        requester_data = json.loads(requester_blob.download_blob().readall())

        # Eliminar la solicitud recibida
        if requester_email in user_data.get("requests", []):
            user_data["requests"].remove(requester_email)

        if accepted:
            # Agregar mutuamente como contactos
            if requester_email not in user_data.get("contacts", []):
                user_data["contacts"].append(requester_email)
            if user_email not in requester_data.get("contacts", []):
                requester_data["contacts"].append(user_email)

        # Guardar los datos actualizados
        user_blob.upload_blob(json.dumps(user_data), overwrite=True)
        requester_blob.upload_blob(json.dumps(requester_data), overwrite=True)

        # 🔔 Emitir actualización en tiempo real
        socketio.emit("contacts_updated", {"email": user_email}, room=user_email)
        socketio.emit(
            "contacts_updated", {"email": requester_email}, room=requester_email
        )

        return jsonify({"message": "Solicitud procesada correctamente"}), 200

    except Exception as e:
        print(f"[ERROR] al responder : {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/delete-chat", methods=["POST"])
def delete_chat():
    data = request.json
    user1 = data.get("user1", "").strip().lower()
    user2 = data.get("user2", "").strip().lower()

    try:
        blob_name = f"chat_{min(user1, user2)}__{max(user1, user2)}.json"
        blob_client = mensajes_container.get_blob_client(blob_name)

        if blob_client.exists():
            blob_client.delete_blob()
            return jsonify({"message": "Chat eliminado correctamente"}), 200
        else:
            return jsonify({"message": "No se encontró el historial de chat"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/get-requests", methods=["POST"])
def get_requests():
    data = request.json
    email = data.get("email", "").strip().lower()

    try:
        blob_client = usuarios_container.get_blob_client(get_blob_name(email))

        if not blob_client.exists():
            return jsonify({"error": "Usuario no encontrado"}), 404

        user_data = json.loads(blob_client.download_blob().readall())
        return jsonify({"requests": user_data.get("requests", [])}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/get-sent-requests", methods=["POST", "GET"])
def get_sent_requests():
    data = request.json
    from_email = data.get("email", "").strip().lower()
    sent = []

    try:
        for blob in usuarios_container.list_blobs():
            blob_client = usuarios_container.get_blob_client(blob)
            content = json.loads(blob_client.download_blob().readall())

            if from_email in content.get("requests", []):
                sent.append(content.get("email"))

        return jsonify({"sent": sent}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/update-profile", methods=["POST"])
def update_profile():
    # NOTA: La validación y cifrado de contraseñas debe hacerse en el frontend.
    # Aquí solo se recibe el hash o valor plano y se compara directamente.
    data = request.json
    print(f"[DEBUG] Datos recibidos para actualizar perfil: {data}")

    email = data.get("email", "").strip().lower()
    current_pw = data.get("current_password", "")
    new_name = data.get("new_name", "").strip()
    new_email = data.get("new_email", "").strip().lower()
    new_password = data.get("new_password", "")

    try:
        blob_name = get_blob_name(email)
        blob_client = usuarios_container.get_blob_client(blob_name)

        if not blob_client.exists():
            return jsonify({"error": "Usuario no encontrado"}), 404

        user_data = json.loads(blob_client.download_blob().readall())

        # Comparar el valor recibido directamente (hash o texto plano)
        if user_data.get("password") != current_pw:
            return jsonify({"error": "Contraseña actual incorrecta"}), 401

        if new_name:
            user_data["name"] = new_name
        if new_password:
            user_data["password"] = new_password

        if new_email and new_email != email:
            new_blob_name = get_blob_name(new_email)
            usuarios_container.get_blob_client(new_blob_name).upload_blob(
                json.dumps(user_data), overwrite=True
            )
            blob_client.delete_blob()
        else:
            blob_client.upload_blob(json.dumps(user_data), overwrite=True)

        return jsonify({"message": "Perfil actualizado correctamente"}), 200

    except Exception as e:
        print("[ERROR]", e)
        return jsonify({"error": str(e)}), 500


@app.route("/cancel-request", methods=["POST"])
def cancel_request():
    data = request.json
    from_email = data.get("from_email", "").strip().lower()
    to_email = data.get("to_email", "").strip().lower()

    try:
        to_blob = usuarios_container.get_blob_client(get_blob_name(to_email))

        if not to_blob.exists():
            return jsonify({"error": "Usuario destinatario no encontrado"}), 404

        to_data = json.loads(to_blob.download_blob().readall())

        if from_email in to_data.get("requests", []):
            to_data["requests"].remove(from_email)
            to_blob.upload_blob(json.dumps(to_data), overwrite=True)
            return jsonify({"message": "Solicitud cancelada correctamente"}), 200
        else:
            return jsonify({"error": "No se encontró una solicitud pendiente"}), 400

    except Exception as e:
        print(f"[ERROR] al cancelar solicitud: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/signup", methods=["POST"])
def signup():
    # NOTA: La validación y cifrado de contraseñas debe hacerse en el frontend.
    # Aquí solo se recibe el hash o valor plano y se almacena directamente.
    data = request.json
    email = data.get("email", "").strip().lower()
    username = data.get("name", "").strip()
    password = data.get("password", "")
    # El campo public_key es obligatorio y debe estar en formato PEM
    public_key_pem = data.get("public_key", "")
    # Validar que la clave pública sea obligatoria y en formato PEM
    if (
        not public_key_pem
        or "-----BEGIN PUBLIC KEY-----" not in public_key_pem
        or "-----END PUBLIC KEY-----" not in public_key_pem
    ):
        return (
            jsonify(
                {
                    "error": "La clave pública es obligatoria y debe estar en formato PEM."
                }
            ),
            400,
        )
    blob_name = get_blob_name(email)

    try:
        if usuarios_container.get_blob_client(blob_name).exists():
            return jsonify({"error": "Este correo ya está registrado"}), 400

        for blob in usuarios_container.list_blobs():
            content = json.loads(
                usuarios_container.get_blob_client(blob).download_blob().readall()
            )
            if content.get("name") == username:
                return jsonify({"error": "Este nombre de usuario ya está en uso"}), 400

        user_data = {
            "name": username,
            "email": email,
            "password": password,
            "contacts": [],
            "requests": [],
            "public_key": public_key_pem,
        }

        usuarios_container.get_blob_client(blob_name).upload_blob(
            json.dumps(user_data), overwrite=True
        )
        return jsonify({"message": "Usuario registrado correctamente"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/change-password", methods=["POST"])
def change_password():
    # NOTA: La validación y cifrado de contraseñas debe hacerse en el frontend.
    # Aquí solo se recibe el hash o valor plano y se compara directamente.
    data = request.json
    email = data.get("email", "").strip().lower()
    current_pw = data.get("current_password", "")
    new_pw = data.get("new_password", "")

    try:
        blob_name = get_blob_name(email)
        blob_client = usuarios_container.get_blob_client(blob_name)

        if not blob_client.exists():
            return jsonify({"error": "Usuario no encontrado"}), 404

        user_data = json.loads(blob_client.download_blob().readall())

        if user_data.get("password") != current_pw:
            return jsonify({"error": "Contraseña actual incorrecta"}), 401

        user_data["password"] = new_pw
        blob_client.upload_blob(json.dumps(user_data), overwrite=True)

        return jsonify({"message": "Contraseña actualizada correctamente"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/login", methods=["POST"])
def login():
    # NOTA: La validación y cifrado de contraseñas debe hacerse en el frontend.
    # Aquí solo se recibe el hash o valor plano y se compara directamente.
    data = request.json
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    try:
        blob_name = get_blob_name(email)
        blob_client = usuarios_container.get_blob_client(blob_name)

        if not blob_client.exists():
            return jsonify({"error": "Usuario no encontrado"}), 404

        user_data = json.loads(blob_client.download_blob().readall())

        if user_data.get("password") == password:
            # Validar que el usuario ya tenga clave pública o que se reciba una nueva válida
            if not user_data.get("public_key") and not (
                "public_key" in data
                and "-----BEGIN PUBLIC KEY-----" in data["public_key"]
                and "-----END PUBLIC KEY-----" in data["public_key"]
            ):
                return (
                    jsonify(
                        {
                            "error": "El usuario debe tener una clave pública asociada. Vuelva a intentarlo desde un navegador compatible."
                        }
                    ),
                    400,
                )
            # Si el frontend envía una clave pública, siempre la actualizamos tras login exitoso.
            # ADVERTENCIA: El campo debe estar en formato PEM: "-----BEGIN PUBLIC KEY----- ... -----END PUBLIC KEY-----"
            public_key_pem = data.get("public_key")
            if public_key_pem:
                # Validar formato PEM básico antes de guardar
                if (
                    "-----BEGIN PUBLIC KEY-----" in public_key_pem
                    and "-----END PUBLIC KEY-----" in public_key_pem
                ):
                    user_data["public_key"] = public_key_pem
                    blob_client.upload_blob(json.dumps(user_data), overwrite=True)
                else:
                    return (
                        jsonify(
                            {
                                "error": "La clave pública enviada no tiene formato PEM válido."
                            }
                        ),
                        400,
                    )
            from flask import make_response
            resp = make_response(jsonify(
                {
                    "message": "Login exitoso",
                    "name": user_data.get("name"),
                    "email": user_data.get("email"),
                }
            ))
            resp.set_cookie('user_email', user_data.get("email"))
            return resp
        else:
            return jsonify({"error": "Contraseña incorrecta"}), 401

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/remove-contact", methods=["POST"])
def remove_contact():
    data = request.json
    user_email = data.get("user_email", "").strip().lower()
    contact_email = data.get("contact_email", "").strip().lower()

    try:
        user_blob = usuarios_container.get_blob_client(get_blob_name(user_email))
        contact_blob = usuarios_container.get_blob_client(get_blob_name(contact_email))

        if not user_blob.exists() or not contact_blob.exists():
            return jsonify({"error": "Uno de los usuarios no existe"}), 404

        user_data = json.loads(user_blob.download_blob().readall())
        contact_data = json.loads(contact_blob.download_blob().readall())

        # Eliminar de la lista de contactos si existen
        if contact_email in user_data.get("contacts", []):
            user_data["contacts"].remove(contact_email)

        if user_email in contact_data.get("contacts", []):
            contact_data["contacts"].remove(user_email)

        # Guardar de nuevo
        user_blob.upload_blob(json.dumps(user_data), overwrite=True)
        contact_blob.upload_blob(json.dumps(contact_data), overwrite=True)
        socketio.emit("contacts_updated", {"email": user_email}, room=user_email)
        socketio.emit("contacts_updated", {"email": contact_email}, room=contact_email)
        return jsonify({"message": "Contacto eliminado correctamente"}), 200

    except Exception as e:
        print(f"[ERROR] al eliminar contacto: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/get-contacts", methods=["POST"])
def get_contacts():
    data = request.json
    user_email = data.get("email", "").strip().lower()

    try:
        blob_name = get_blob_name(user_email)
        user_blob = usuarios_container.get_blob_client(blob_name)

        if not user_blob.exists():
            return jsonify({"error": "Usuario no encontrado"}), 404

        user_data = json.loads(user_blob.download_blob().readall())
        contact_emails = user_data.get("contacts", [])
        contact_list = []

        # Agrega en el loop de contactos
        for email in contact_emails:
            contact_blob = usuarios_container.get_blob_client(get_blob_name(email))
            if contact_blob.exists():
                contact_data = json.loads(contact_blob.download_blob().readall())

                # Calcular mensajes no leídos
                chat_blob = mensajes_container.get_blob_client(
                    f"chat_{min(user_email, email)}__{max(user_email, email)}.json"
                )
                unread = 0
                if chat_blob.exists():
                    chat_messages = json.loads(chat_blob.download_blob().readall())
                    unread = sum(
                        1
                        for m in chat_messages
                        if m.get("to") == user_email and not m.get("read", False)
                    )

                contact_list.append(
                    {
                        "name": contact_data.get("name"),
                        "email": contact_data.get("email"),
                        "unread": unread,
                    }
                )

        return (
            jsonify(
                {"contacts": contact_list, "active_with": user_data.get("active_with")}
            ),
            200,
        )

    except Exception as e:
        print(f"[ERROR] al obtener contactos: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/get-messages", methods=["POST"])
def get_messages():
    data = request.json
    user1 = data.get("user1", "").strip().lower()
    user2 = data.get("user2", "").strip().lower()

    blob_name = f"chat_{min(user1, user2)}__{max(user1, user2)}.json"
    blob_client = mensajes_container.get_blob_client(blob_name)

    try:
        if not blob_client.exists():
            return jsonify({"messages": []}), 200

        messages = json.loads(blob_client.download_blob().readall())
        return jsonify({"messages": messages}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/update-public-key", methods=["POST"])
def update_public_key():
    data = request.json
    email = data.get("email", "").strip().lower()
    public_key_pem = data.get("public_key")

    if not email or not public_key_pem:
        return jsonify({"error": "Faltan datos requeridos"}), 400

    try:
        blob_name = get_blob_name(email)
        blob_client = usuarios_container.get_blob_client(blob_name)

        if not blob_client.exists():
            return jsonify({"error": "Usuario no encontrado"}), 404

        user_data = json.loads(blob_client.download_blob().readall())
        user_data["public_key"] = public_key_pem

        blob_client.upload_blob(json.dumps(user_data), overwrite=True)

        return jsonify({"message": "Clave pública actualizada correctamente"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/mark-read", methods=["POST"])
def mark_messages_as_read():
    data = request.json
    user1 = data.get("user1", "").strip().lower()
    user2 = data.get("user2", "").strip().lower()

    blob_name = f"chat_{min(user1, user2)}__{max(user1, user2)}.json"
    blob_client = mensajes_container.get_blob_client(blob_name)

    try:
        if not blob_client.exists():
            return jsonify({"message": "No hay mensajes que marcar"}), 200

        messages = json.loads(blob_client.download_blob().readall())
        updated = False

        for msg in messages:
            if msg["to"] == user1 and not msg.get("read", False):
                msg["read"] = True
                updated = True

        if updated:
            blob_client.upload_blob(json.dumps(messages), overwrite=True)

        return jsonify({"message": "Mensajes marcados como leídos"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500



# Endpoint para solicitar el envío del código de recuperación
@app.route("/request-reset-code", methods=["POST"])
def request_reset_code():
    data = request.json
    email = data.get("email", "").strip().lower()
    blob_name = get_blob_name(email)
    blob_client = usuarios_container.get_blob_client(blob_name)
    if not blob_client.exists():
        return jsonify({"error": "El correo no está registrado."}), 404

    code = str(random.randint(100000, 999999))
    password_reset_codes[email] = {
        "code": code,
        "expires": datetime.datetime.utcnow() + datetime.timedelta(minutes=10)
    }
    try:
        send_recovery_email(email, code)
        return jsonify({"message": "Se ha enviado un código de recuperación a tu correo"}), 200
    except Exception as e:
        print("[ERROR al enviar correo]", e)
        # Devuelve el mensaje real de error para depuración (solo durante desarrollo).
        return jsonify({"error": f"No se pudo enviar el correo: {str(e)}"}), 500

# Endpoint para verificar el código y cambiar la contraseña
@app.route("/verify-reset-code", methods=["POST"])
def verify_reset_code():
    data = request.json
    email = data.get("email", "").strip().lower()
    code = data.get("code", "")
    new_password = data.get("new_password", "")
    if not (email and code and new_password):
        return jsonify({"error": "Datos incompletos"}), 400

    record = password_reset_codes.get(email)
    if not record or record["code"] != code:
        return jsonify({"error": "Código incorrecto"}), 400
    if datetime.datetime.utcnow() > record["expires"]:
        return jsonify({"error": "El código ha expirado"}), 400

    # Cambiar la contraseña
    blob_name = get_blob_name(email)
    blob_client = usuarios_container.get_blob_client(blob_name)
    if not blob_client.exists():
        return jsonify({"error": "El correo no está registrado."}), 404
    user_data = json.loads(blob_client.download_blob().readall())
    user_data["password"] = new_password
    blob_client.upload_blob(json.dumps(user_data), overwrite=True)

    del password_reset_codes[email]
    return jsonify({"message": "Contraseña cambiada correctamente"}), 200

# ==== GRUPOS ====
@app.route("/create-group", methods=["POST"])
def create_group():
    data = request.json
    group_name = data.get("name")
    members = data.get("members", [])
    group_id = f"group_{uuid.uuid4().hex[:8]}"
    group_blob_name = f"{group_id}.json"

    group_data = {
        "id": group_id,
        "name": group_name,
        "members": members,
        "created_at": datetime.datetime.utcnow().isoformat(),
    }
    try:
        mensajes_container.get_blob_client(group_blob_name).upload_blob(
            json.dumps(group_data), overwrite=True
        )
        # Historial de chat vacío
        mensajes_container.get_blob_client(f"chat_{group_id}.json").upload_blob(
            json.dumps([]), overwrite=True
        )
        return jsonify({"group_id": group_id, "message": "Grupo creado"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/get-groups", methods=["POST"])
def get_groups():
    data = request.json
    email = data.get("email", "").strip().lower()
    groups = []
    try:
        for blob in mensajes_container.list_blobs():
            if blob.name.startswith("group_") and blob.name.endswith(".json"):
                group_data = json.loads(
                    mensajes_container.get_blob_client(blob).download_blob().readall()
                )
                if email in group_data.get("members", []):
                    groups.append(group_data)
        return jsonify({"groups": groups}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@socketio.on("send_group_message")
def handle_group_message(data):
    group_id = data["group_id"]
    sender = data["from"]
    content = data["message"]
    chat_blob_name = f"chat_{group_id}.json"

    try:
        blob_client = mensajes_container.get_blob_client(chat_blob_name)
        all_messages = []
        if blob_client.exists():
            all_messages = json.loads(blob_client.download_blob().readall())

        msg_to_store = {
            "from": sender,
            "message": content,
            "timestamp": datetime.datetime.utcnow().isoformat(),
        }
        all_messages.append(msg_to_store)
        blob_client.upload_blob(json.dumps(all_messages), overwrite=True)
        socketio.emit("receive_group_message", msg_to_store, room=group_id)
    except Exception as e:
        print(f"[ERROR grupo] {e}")



@socketio.on("join_group")
def join_group(data):
    group_id = data["group_id"]
    join_room(group_id)
    # Cargar historial
    chat_blob_name = f"chat_{group_id}.json"
    try:
        blob_client = mensajes_container.get_blob_client(chat_blob_name)
        messages = []
        if blob_client.exists():
            messages = json.loads(blob_client.download_blob().readall())
        emit("group_chat_history", messages, room=request.sid)
    except Exception as e:
        emit("group_chat_history", [], room=request.sid)


# Endpoint para añadir miembros a un grupo existente
@app.route("/add-group-members", methods=["POST"])
def add_group_members():
    data = request.json
    group_id = data.get("group_id")
    new_members = data.get("new_members", [])

    if not group_id or not new_members:
        return jsonify({"error": "Faltan datos requeridos"}), 400

    group_blob_name = f"{group_id}.json"
    try:
        group_blob = mensajes_container.get_blob_client(group_blob_name)
        if not group_blob.exists():
            return jsonify({"error": "Grupo no encontrado"}), 404

        group_data = json.loads(group_blob.download_blob().readall())
        original_members = set(group_data.get("members", []))
        added = []
        for member in new_members:
            if member not in original_members:
                group_data["members"].append(member)
                added.append(member)
        group_blob.upload_blob(json.dumps(group_data), overwrite=True)
        return jsonify({"message": f"Se añadieron {len(added)} miembro(s) al grupo.", "added": added}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/<path:filename>")
def dynamic_static(filename):
    if filename.startswith("logsign/"):
        return send_from_directory("source/logsign", filename[len("logsign/"):])
    elif filename.startswith("index/"):
        return send_from_directory("source/index", filename[len("index/"):])
    else:
        return send_from_directory("source", filename)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    socketio.run(app, host="10.48.73.169", port=5050, debug=True)

