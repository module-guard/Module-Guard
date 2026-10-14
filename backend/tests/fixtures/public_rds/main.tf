resource "aws_db_instance" "insecure" {
  allocated_storage   = 10
  engine              = "mysql"
  instance_class      = "db.t3.micro"
  username            = "testadmin"
  publicly_accessible = true
  storage_encrypted   = false
  skip_final_snapshot = true
}
