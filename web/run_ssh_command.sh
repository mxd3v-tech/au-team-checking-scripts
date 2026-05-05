#!/bin/bash

# Аргументы:
# $1 - порт SSH
# $2 - имя пользователя
# $3 - пароль
# $4 - команда для выполнения

PORT=$1
USERNAME=$2
PASSWORD=$3
COMMAND=$4

# Команда для выполнения
SSH_CMD="sshpass -t -p '$PASSWORD' ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p $PORT $USERNAME@localhost"

# Выполняем команду с задержкой
echo "Подключение к $USERNAME@localhost:$PORT..."
$SSH_CMD << EOF
# Ждем 5 секунд (можно увеличить при необходимости)
sleep 30
# Выполняем команду
$COMMAND
EOF
