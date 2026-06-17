package main

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"
	"unicode/utf8"

	"github.com/joho/godotenv"
	_ "github.com/lib/pq"
)

// Структуры для API Telegram
type TelegramMessage struct {
	ChatID      int64           `json:"chat_id"`
	Text        string          `json:"text"`
	ParseMode   string          `json:"parse_mode"`
	ReplyMarkup json.RawMessage `json:"reply_markup,omitempty"`
}

type TelegramPhoto struct {
	ChatID      int64           `json:"chat_id"`
	Photo       string          `json:"photo"`
	Caption     string          `json:"caption,omitempty"`
	ParseMode   string          `json:"parse_mode"`
	ReplyMarkup json.RawMessage `json:"reply_markup,omitempty"`
}

// Структура задачи из БД
type Task struct {
	ID            int64
	UserID        int64
	TextContent   string
	ImageURL      sql.NullString
	InlineButtons sql.NullString
}

var (
	botToken   string
	httpClient = &http.Client{Timeout: 10 * time.Second}
)

func main() {
	// 1. Загрузка переменных окружения (из файла .env на уровень выше, если нужно)
	cwd, _ := os.Getwd()
	log.Printf("📍 Текущая директория: %s", cwd)

	if err := godotenv.Load("../.env"); err != nil {
		log.Printf("⚠️  Не удалось загрузить ../.env: %v", err)
	} else {
		log.Printf("✅ Файл ../.env успешно загружен")
	}

	botToken = os.Getenv("BOT_TOKEN")
	dbDsn := os.Getenv("DB_DSN")

	log.Printf("🔐 BOT_TOKEN: %s...%s", botToken[:10], botToken[len(botToken)-5:])
	log.Printf("🗄️  DB_DSN: %s...%s", dbDsn[:30], dbDsn[len(dbDsn)-20:])

	if botToken == "" || dbDsn == "" {
		log.Fatal("❌ BOT_TOKEN или DB_DSN не заданы")
	}

	// Адаптация DSN для стандартного драйвера pq (удаляем postgresql+asyncpg://)
	dbDsn = strings.Replace(dbDsn, "postgresql+asyncpg://", "postgres://", 1)

	// 2. Подключение к БД
	db, err := sql.Open("postgres", dbDsn)
	if err != nil {
		log.Fatalf("Ошибка подключения к БД: %v", err)
	}
	defer db.Close()

	if err = db.Ping(); err != nil {
		log.Fatalf("БД недоступна: %v", err)
	}

	// 3. Настройка Graceful Shutdown
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)

	go func() {
		<-sigChan
		log.Println("Получен сигнал завершения. Ожидание окончания текущих задач...")
		cancel()
	}()

	log.Println("🤖 Go-воркер дожимов успешно запущен...")

	// 4. Главный цикл (тикер 15 секунд)
	ticker := time.NewTicker(15 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			log.Println("Воркер остановлен.")
			return
		case <-ticker.C:
			processQueue(ctx, db)
		}
	}
}

// processQueue забирает пачку задач и отправляет их
func processQueue(ctx context.Context, db *sql.DB) {
	// Начинаем транзакцию для безопасной блокировки строк
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		log.Printf("Ошибка старта транзакции: %v", err)
		return
	}
	defer tx.Rollback()

	// Забираем до 50 сообщений, чье время пришло, блокируя их от других процессов
	query := `
		SELECT id, user_id, text_content, image_url, inline_buttons
		FROM scheduled_messages
		WHERE status = 'pending' AND send_at <= NOW()
		ORDER BY send_at ASC
		LIMIT 50
		FOR UPDATE SKIP LOCKED;
	`
	rows, err := tx.QueryContext(ctx, query)
	if err != nil {
		log.Printf("Ошибка выборки задач: %v", err)
		return
	}
	defer rows.Close()

	var tasks []Task
	for rows.Next() {
		var t Task
		if err := rows.Scan(&t.ID, &t.UserID, &t.TextContent, &t.ImageURL, &t.InlineButtons); err != nil {
			log.Printf("Ошибка парсинга строки: %v", err)
			continue
		}
		tasks = append(tasks, t)
	}

	if len(tasks) == 0 {
		return // Очередь пуста
	}

	log.Printf("Найдено задач: %d. Начинаю рассылку...", len(tasks))

	// Пул горутин для параллельной отправки
	var wg sync.WaitGroup
	for _, task := range tasks {
		wg.Add(1)
		go func(t Task) {
			defer wg.Done()

			success := sendTelegramMessage(t)

			status := "failed"
			if success {
				status = "sent"
			}

			// Обновляем статус в БД вне основной транзакции выборки
			_, err := db.ExecContext(ctx, "UPDATE scheduled_messages SET status = $1 WHERE id = $2", status, t.ID)
			if err != nil {
				log.Printf("Ошибка обновления статуса для задачи %d: %v", t.ID, err)
			}
		}(task)
	}

	wg.Wait()
	_ = tx.Commit()
}

// sendTelegramMessage содержит логику обхода лимитов и вызовов API
func sendTelegramMessage(t Task) bool {
	// Подготовка текста (как в catch_up_user)
	cleanText := strings.ReplaceAll(t.TextContent, "\\n", "\n")

	// Подготовка кнопок
	var replyMarkup json.RawMessage
	if t.InlineButtons.Valid && t.InlineButtons.String != "" {
		replyMarkup = json.RawMessage(t.InlineButtons.String)
	}

	hasImage := t.ImageURL.Valid && t.ImageURL.String != ""

	if hasImage {
		// Проверка лимита Telegram (1024 символа)
		if utf8.RuneCountInString(cleanText) <= 1024 {
			// Отправляем одной картинкой с подписью
			payload := TelegramPhoto{
				ChatID:      t.UserID,
				Photo:       t.ImageURL.String,
				Caption:     cleanText,
				ParseMode:   "HTML",
				ReplyMarkup: replyMarkup,
			}
			return sendRequest("sendPhoto", payload)
		} else {
			// Текст слишком длинный: сначала голая картинка, потом текст с кнопками
			photoPayload := TelegramPhoto{
				ChatID:    t.UserID,
				Photo:     t.ImageURL.String,
				ParseMode: "HTML",
			}
			if !sendRequest("sendPhoto", photoPayload) {
				return false // Если картинка не ушла, прерываем
			}

			textPayload := TelegramMessage{
				ChatID:      t.UserID,
				Text:        cleanText,
				ParseMode:   "HTML",
				ReplyMarkup: replyMarkup,
			}
			return sendRequest("sendMessage", textPayload)
		}
	}

	// Если картинки нет, отправляем просто текст
	payload := TelegramMessage{
		ChatID:      t.UserID,
		Text:        cleanText,
		ParseMode:   "HTML",
		ReplyMarkup: replyMarkup,
	}
	return sendRequest("sendMessage", payload)
}

// sendRequest делает непосредственный HTTP POST к Telegram API
func sendRequest(method string, payload interface{}) bool {
	url := fmt.Sprintf("https://api.telegram.org/bot%s/%s", botToken, method)

	body, err := json.Marshal(payload)
	if err != nil {
		log.Printf("Ошибка маршалинга JSON (%s): %v", method, err)
		return false
	}

	req, err := http.NewRequest("POST", url, bytes.NewBuffer(body))
	if err != nil {
		return false
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := httpClient.Do(req)
	if err != nil {
		log.Printf("Сетевая ошибка при отправке (%s): %v", method, err)
		return false
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		log.Printf("Telegram API вернул ошибку (%s): код %d", method, resp.StatusCode)
		return false
	}

	return true
}
