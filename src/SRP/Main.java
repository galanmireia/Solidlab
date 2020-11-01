package SRP;

public class Main {

	public static void main(String[] args) {
		// TODO Auto-generated method stub

		Covid19Pacient prueba = new Covid19Pacient("Mireia", 22);

		NeuroMuscularSymptom sintoma1 = new NeuroMuscularSymptom(80, 2, "a", 2.0);
		CardioVascularSymptom sintoma2 = new CardioVascularSymptom(90, 20, "b", 3.0);
		RespiratorySymptom sintoma3 = new RespiratorySymptom(4, 10, "c", 4.0);

		prueba.addSymptom(sintoma1, 0);
		prueba.addSymptom(sintoma2, 1);
		prueba.addSymptom(sintoma3, 2);

		System.out.println(prueba.calcCovid19Impact());
		System.out.println(prueba.sanatedDays());

	}

}
