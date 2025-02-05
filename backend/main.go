package main

import (
	"database/sql"
	"encoding/json"
	"github.com/joho/godotenv"
	"log"
	"net/http"
	"os"
	"historian/backend/templates"

	_ "github.com/mattn/go-sqlite3"
	"github.com/gorilla/mux"
)

var db *sql.DB

func main() {
	err := godotenv.Load()
	if err != nil {
		log.Fatal("Error loading .env file")
	}

	// Connect to SQLite database
	db, err = sql.Open("sqlite3", "./video_compilation.db")
	if err != nil {
		log.Fatal("Error connecting to database:", err)
	}
	defer db.Close()

	// Create users table if it doesn't exist
	_, err = db.Exec(`
		CREATE TABLE IF NOT EXISTS users (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			email TEXT UNIQUE NOT NULL,
			password TEXT NOT NULL,
			role TEXT NOT NULL DEFAULT 'user'
		);
	`)
	if err != nil {
		log.Fatal("Error creating users table:", err)
	}

	r := mux.NewRouter()

	// Serve static files
	r.PathPrefix("/static/").Handler(http.StripPrefix("/static/", http.FileServer(http.Dir("frontend/static"))))

	// Routes
	r.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		templates.Login().Render(r.Context(), w)
	}).Methods("GET")

  r.Handle("/admin", authMiddleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
      users := getUsers()
      templates.Admin(users).Render(r.Context(), w)
  }))).Methods("GET")

	r.HandleFunc("/api/auth/setup", setupAdmin).Methods("POST")
	r.HandleFunc("/api/auth/login", login).Methods("POST")
	r.HandleFunc("/api/auth/users", addUser).Methods("POST")

	log.Println("Server started on :8080")
	log.Fatal(http.ListenAndServe(":8080", r))
}

func getUsers() []User {
	var users []User
	rows, err := db.Query("SELECT id, email, role FROM users")
	if err != nil {
		log.Println("Error fetching users:", err)
		return users
	}
	defer rows.Close()

	for rows.Next() {
		var user User
		err := rows.Scan(&user.ID, &user.Email, &user.Role)
		if err != nil {
			log.Println("Error scanning user:", err)
			continue
		}
		users = append(users, user)
	}
	return users
}

func setupAdmin(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Email    string `json:"email"`
		Password string `json:"password"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Invalid request", http.StatusBadRequest)
		return
	}

	if req.Email != os.Getenv("ADMIN_EMAIL") || req.Password != os.Getenv("ADMIN_PASSWORD") {
		http.Error(w, "Invalid admin credentials", http.StatusUnauthorized)
		return
	}

	hashedPassword, err := hashPassword(req.Password)
	if err != nil {
		http.Error(w, "Error hashing password", http.StatusInternalServerError)
		return
	}

	_, err = db.Exec("INSERT INTO users (email, password, role) VALUES (?, ?, ?)", req.Email, hashedPassword, "admin")
	if err != nil {
		http.Error(w, "Error creating admin user", http.StatusInternalServerError)
		return
	}

	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(map[string]string{"message": "Admin user created"})
}

func login(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Email    string `json:"email"`
		Password string `json:"password"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Invalid request", http.StatusBadRequest)
		return
	}

	var user User
	err := db.QueryRow("SELECT id, email, password, role FROM users WHERE email = ?", req.Email).Scan(&user.ID, &user.Email, &user.Password, &user.Role)
	if err != nil {
		http.Error(w, "Invalid credentials", http.StatusUnauthorized)
		return
	}

	if !checkPasswordHash(req.Password, user.Password) {
		http.Error(w, "Invalid credentials", http.StatusUnauthorized)
		return
	}

	token, err := createToken(user, os.Getenv("JWT_SECRET"))
	if err != nil {
		http.Error(w, "Error creating token", http.StatusInternalServerError)
		return
	}

	json.NewEncoder(w).Encode(map[string]string{"token": token})
}

func addUser(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Email    string `json:"email"`
		Password string `json:"password"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Invalid request", http.StatusBadRequest)
		return
	}

	hashedPassword, err := hashPassword(req.Password)
	if err != nil {
		http.Error(w, "Error hashing password", http.StatusInternalServerError)
		return
	}

	_, err = db.Exec("INSERT INTO users (email, password) VALUES (?, ?)", req.Email, hashedPassword)
	if err != nil {
		http.Error(w, "Error creating user", http.StatusInternalServerError)
		return
	}

	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(map[string]string{"message": "User created"})
}
