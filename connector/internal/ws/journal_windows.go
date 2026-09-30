//go:build windows

package ws

// Windows does not support fsync on directory handles via os.File.
func syncJournalDir(string) error { return nil }
