package LSK;



public class main {

	public static void main(String[] args) {
		// TODO Auto-generated method stub

		Covid19Pacient prueba = new Covid19Pacient("Mireia", 22);

		NeuroMuscularSymptom sintoma1 = new NeuroMuscularSymptom(1, 2, "a");
		CardioVascularSymptom sintoma2 = new CardioVascularSymptom(10, 20, "b");
		RespiratorySymptom sintoma3 = new RespiratorySymptom(4, 10, "c");
		//IncurableSymptom sintoma4 = new IncurableSymptom(20, 1, "triste");
		
		

		prueba.addSymptom(sintoma1, 0);
		prueba.addSymptom(sintoma2, 1);
		prueba.addSymptom(sintoma3, 2);
		//prueba.addSymptom(sintoma4, 3);
		
		prueba.cure(); //heredatzen duen klase bakoitza guraso gisa erabil daiteke haien arteko desberdintasunak jakin beharrik izan gabe
		
	}

}
